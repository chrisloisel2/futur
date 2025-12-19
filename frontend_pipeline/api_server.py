"""
API SERVER FOR ALPHA DASHBOARD
===============================
Serveur FastAPI pour exposer les données de trading alpha au frontend React.
"""
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import uvicorn
import logging
import sys
import random
import subprocess
import threading
import glob
from pydantic import BaseModel


from mongo_utils import fetch_historical_from_mongo, normalize_symbol, get_db
from pymongo.errors import PyMongoError

# Add TRAIN to path for S3 access
sys.path.insert(0, str(Path(__file__).parent.parent / "ai" / "TRAIN"))
from data.s3_data_source import S3DataSource

# Import data integrity analyzer
from data_integrity_analyzer import DataIntegrityAnalyzer

app = FastAPI(title="Alpha Trading API", version="2.0")

# ============================================================================
# TRAINING JOB MANAGEMENT
# ============================================================================

# Training jobs storage (in-memory)
training_jobs = {}  # {job_id: {...job_info...}}
training_lock = threading.Lock()


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

PORTFOLIO_COLLECTION = os.getenv("PORTFOLIO_COLLECTION", "portfolio_state")
PORTFOLIO_DOC_ID = "default"
PORTFOLIO_INITIAL_CAPITAL = float(os.getenv("PORTFOLIO_INITIAL_CAPITAL", "10000"))


# CORS pour permettre les requêtes depuis React
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # React dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_latest_dataset_path() -> Path:
    """Trouver le dataset le plus récent."""
    datasets_path = Path("datasets/alpha_trading")
    if not datasets_path.exists():
        raise HTTPException(status_code=404, detail="No datasets found")

    dataset_folders = sorted(datasets_path.glob("dataset_*"), reverse=True)
    if not dataset_folders:
        raise HTTPException(status_code=404, detail="No dataset folders found")

    return dataset_folders[0]

@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Alpha Trading API",
        "version": "2.0",
        "status": "operational",
        "endpoints": [
            "/dataset/summary",
            "/dataset/signals",
            "/dataset/ohlcv/{symbol}",
            "/dataset/funding-rates",
            "/dataset/fear-greed",
            "/dataset/sentiment",
            "/dataset/macro",
            "/dataset/derivatives",
            "/market/all-cryptos",
            "/market/ticker",
            "/market/klines",
            "/market/orderbook",
            "/market/trades",
            "/portfolio/state",
            "/portfolio/trade",
            "/portfolio/reset",
        ]
    }

@app.get("/dataset/summary")
async def get_dataset_summary():
    """Récupérer le résumé du dataset."""
    dataset_path = get_latest_dataset_path()

    # Charger metadata
    metadata_file = dataset_path / "metadata.json"
    if metadata_file.exists():
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)
    else:
        metadata = {}

    # Compter les records par source
    data_sources = {}
    for parquet_file in dataset_path.glob("*.parquet"):
        try:
            df = pd.read_parquet(parquet_file)
            data_sources[parquet_file.stem] = {
                "records": len(df),
                "columns": list(df.columns),
                "size_mb": parquet_file.stat().st_size / (1024 * 1024)
            }
        except Exception as e:
            data_sources[parquet_file.stem] = {"error": str(e)}

    return {
        "dataset_name": dataset_path.name,
        "metadata": metadata,
        "data_sources": data_sources,
        "total_records": sum(
            source.get("records", 0)
            for source in data_sources.values()
        )
    }


# ============================================================================
# PORTFOLIO MANAGEMENT (MongoDB)
# ============================================================================

class TradeRequest(BaseModel):
    symbol: str
    action: str
    price: float
    confidence: Optional[float] = 0.5
    reason: Optional[str] = None

class TrainingStartRequest(BaseModel):
    config: str
    device: str = "auto"
    debug_mode: bool = False
    training_location: str = "aws"  # "aws", "remote", or "local"
    instance_type: str = "g4dn.xlarge"  # GPU T4, ~$0.50/h (for AWS)
    aws_region: str = "eu-west-3"  # AWS region (for AWS)
    remote_host: str = "100.118.183.51"  # Remote server host (for remote)
    remote_user: str = "qbee"  # Remote server SSH user (for remote)


# ============================================================================
# TRAINING HELPER FUNCTIONS
# ============================================================================

def parse_training_log(log_file_path: str) -> Dict[str, any]:
    """Parse training log file to extract current metrics."""
    metrics = {
        "current_epoch": 0,
        "total_epochs": 0,
        "train_loss": 0.0,
        "val_loss": 0.0,
        "val_sharpe": 0.0,
        "learning_rate": 0.0
    }

    if not Path(log_file_path).exists():
        return metrics

    try:
        with open(log_file_path, 'r') as f:
            lines = f.readlines()

        # Parse from the end of file (most recent metrics)
        for line in reversed(lines[-50:]):  # Check last 50 lines
            # Pattern: Epoch 5/50 | Train Loss: 0.452341 | Val Loss: 0.389234 | Val Sharpe: 1.2345 | LR: 1.00e-04
            if "Epoch" in line and "/" in line:
                import re
                epoch_match = re.search(r'Epoch (\d+)/(\d+)', line)
                if epoch_match:
                    metrics["current_epoch"] = int(epoch_match.group(1))
                    metrics["total_epochs"] = int(epoch_match.group(2))

                train_loss_match = re.search(r'Train Loss: ([\d.]+)', line)
                if train_loss_match:
                    metrics["train_loss"] = float(train_loss_match.group(1))

                val_loss_match = re.search(r'Val Loss: ([\d.]+)', line)
                if val_loss_match:
                    metrics["val_loss"] = float(val_loss_match.group(1))

                sharpe_match = re.search(r'Val Sharpe: ([\d.]+)', line)
                if sharpe_match:
                    metrics["val_sharpe"] = float(sharpe_match.group(1))

                lr_match = re.search(r'LR: ([\d.e-]+)', line)
                if lr_match:
                    metrics["learning_rate"] = float(lr_match.group(1))

                break  # Found the most recent epoch line

    except Exception as e:
        logger.error(f"Error parsing training log: {e}")

    return metrics


def monitor_training_process(job_id: str):
    """Background thread to monitor training process and update job status."""
    with training_lock:
        if job_id not in training_jobs:
            return
        job = training_jobs[job_id]

    process = job["process"]

    try:
        while True:
            # Check if process is still running
            poll_result = process.poll()

            if poll_result is not None:
                # Process has finished
                with training_lock:
                    job["end_time"] = datetime.utcnow()
                    if poll_result == 0:
                        job["status"] = "completed"
                        logger.info(f"Training job {job_id} completed successfully")
                    else:
                        job["status"] = "failed"
                        job["error"] = f"Process exited with code {poll_result}"
                        logger.error(f"Training job {job_id} failed with code {poll_result}")

                    # Save final metadata
                    save_training_metadata(job_id)
                break

            # Update metrics from log file
            metrics = parse_training_log(job["log_file"])
            with training_lock:
                job["current_epoch"] = metrics["current_epoch"]
                job["total_epochs"] = metrics["total_epochs"] or job["total_epochs"]
                job["current_loss"] = metrics["train_loss"]
                job["current_val_loss"] = metrics["val_loss"]
                job["current_sharpe"] = metrics["val_sharpe"]

                if job["total_epochs"] > 0:
                    job["progress_pct"] = (metrics["current_epoch"] / job["total_epochs"]) * 100.0

            # Sleep for 2 seconds before next check
            threading.Event().wait(2.0)

    except Exception as e:
        logger.error(f"Error monitoring training job {job_id}: {e}")
        with training_lock:
            job["status"] = "failed"
            job["error"] = str(e)


def save_training_metadata(job_id: str):
    """Save training job metadata to JSON file."""
    with training_lock:
        if job_id not in training_jobs:
            return
        job = training_jobs[job_id].copy()

    # Remove non-serializable fields
    job.pop("process", None)

    # Convert datetime objects
    if "start_time" in job and isinstance(job["start_time"], datetime):
        job["start_time"] = job["start_time"].isoformat()
    if "end_time" in job and isinstance(job["end_time"], datetime):
        job["end_time"] = job["end_time"].isoformat()

    # Save to JSON file alongside checkpoint
    checkpoints_dir = Path(__file__).parent.parent / "ai" / "checkpoints_light"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)

    metadata_file = checkpoints_dir / f"{job_id}_metadata.json"
    try:
        with open(metadata_file, 'w') as f:
            json.dump(job, f, indent=2)
        logger.info(f"Saved metadata for job {job_id} to {metadata_file}")
    except Exception as e:
        logger.error(f"Error saving metadata: {e}")


def get_available_configs() -> List[str]:
    """Get list of available training configurations."""
    configs_dir = Path(__file__).parent.parent / "ai" / "configs"
    if not configs_dir.exists():
        return []

    config_files = list(configs_dir.glob("train_*.yaml"))
    return [f.name for f in sorted(config_files)]


# ============================================================================
# AWS TRAINING HELPER FUNCTIONS
# ============================================================================

def launch_aws_training(job_id: str, config: str, instance_type: str, aws_region: str, debug_mode: bool) -> Dict:
    """Launch training on AWS EC2 via shell script wrapper."""

    # Path to the launch script
    script_path = Path(__file__).parent.parent / "ai" / "scripts" / "launch_aws_training.sh"

    if not script_path.exists():
        raise FileNotFoundError(f"AWS launch script not found: {script_path}")

    # Build command
    config_name = config.replace(".yaml", "")  # Script adds .yaml automatically
    cmd = [
        "bash",
        str(script_path),
        config_name,
        instance_type
    ]

    # Environment variables
    env = os.environ.copy()
    env["AWS_REGION"] = aws_region
    env["KEY_NAME"] = "trading-ml-key"
    env["SECURITY_GROUP"] = "trading-ml-sg"
    env["S3_BUCKET"] = "qbia"

    # Log file for the launch script output
    log_file = Path(f"/tmp/training_aws_{job_id}.log")

    # Launch the process
    logger.info(f"Launching AWS training: {' '.join(cmd)}")
    process = subprocess.Popen(
        cmd,
        env=env,
        stdout=open(log_file, 'w'),
        stderr=subprocess.STDOUT,
        cwd=str(script_path.parent.parent)
    )

    return {
        "process": process,
        "log_file": str(log_file),
        "is_aws": True,
        "instance_type": instance_type,
        "aws_region": aws_region
    }


def parse_aws_instance_info(job_id: str) -> Optional[Dict]:
    """Parse AWS instance info from the JSON file created by launch script."""

    info_file = Path("/tmp/aws_training_instance.json")

    if not info_file.exists():
        return None

    try:
        with open(info_file, 'r') as f:
            data = json.load(f)

        return {
            "instance_id": data.get("instance_id"),
            "public_ip": data.get("public_ip"),
            "instance_type": data.get("instance_type"),
            "s3_models_path": data.get("s3_models_path"),
            "launched_at": data.get("launched_at")
        }
    except Exception as e:
        logger.error(f"Error parsing AWS instance info: {e}")
        return None


def parse_training_log_from_text(log_text: str) -> Dict[str, any]:
    """Parse training metrics from log text (used for SSH logs)."""
    import re

    metrics = {
        "current_epoch": 0,
        "total_epochs": 0,
        "train_loss": 0.0,
        "val_loss": 0.0,
        "val_sharpe": 0.0,
        "learning_rate": 0.0
    }

    try:
        lines = log_text.split('\n')

        # Parse from the end (most recent metrics)
        for line in reversed(lines[-50:]):
            if "Epoch" in line and "/" in line:
                epoch_match = re.search(r'Epoch (\d+)/(\d+)', line)
                if epoch_match:
                    metrics["current_epoch"] = int(epoch_match.group(1))
                    metrics["total_epochs"] = int(epoch_match.group(2))

                train_loss_match = re.search(r'Train Loss: ([\d.]+)', line)
                if train_loss_match:
                    metrics["train_loss"] = float(train_loss_match.group(1))

                val_loss_match = re.search(r'Val Loss: ([\d.]+)', line)
                if val_loss_match:
                    metrics["val_loss"] = float(val_loss_match.group(1))

                sharpe_match = re.search(r'Val Sharpe: ([\d.]+)', line)
                if sharpe_match:
                    metrics["val_sharpe"] = float(sharpe_match.group(1))

                lr_match = re.search(r'LR: ([\d.e-]+)', line)
                if lr_match:
                    metrics["learning_rate"] = float(lr_match.group(1))

                break  # Found the most recent epoch line

    except Exception as e:
        logger.error(f"Error parsing training log text: {e}")

    return metrics


def monitor_aws_training(job_id: str):
    """Background thread to monitor AWS training job."""
    with training_lock:
        if job_id not in training_jobs:
            return
        job = training_jobs[job_id]

    # Wait for AWS instance info to become available
    max_wait = 300  # 5 minutes
    waited = 0
    aws_info = None

    logger.info(f"Waiting for AWS instance info for job {job_id}...")

    while waited < max_wait and aws_info is None:
        aws_info = parse_aws_instance_info(job_id)
        if aws_info:
            break
        threading.Event().wait(10)
        waited += 10

    if not aws_info:
        logger.error(f"Failed to get AWS instance info for job {job_id}")
        with training_lock:
            job["status"] = "failed"
            job["error"] = "Failed to get AWS instance info after 5 minutes"
        return

    # Update job with AWS info
    logger.info(f"AWS instance launched: {aws_info['instance_id']} at {aws_info['public_ip']}")
    with training_lock:
        job["aws_instance_id"] = aws_info["instance_id"]
        job["aws_public_ip"] = aws_info["public_ip"]
        job["aws_s3_path"] = aws_info["s3_models_path"]
        job["status"] = "running"  # Change from "launching" to "running"

    # Monitor via SSH
    instance_ip = aws_info["public_ip"]
    key_path = os.path.expanduser("~/.ssh/trading-ml-key.pem")

    # Wait a bit for SSH to be available
    logger.info(f"Waiting for SSH access to {instance_ip}...")
    threading.Event().wait(30)

    while True:
        # Check if local process (launch script) is still running
        process = job.get("process")
        if process and process.poll() is not None:
            # Launch script has finished
            with training_lock:
                if process.returncode == 0:
                    job["status"] = "completed"
                    logger.info(f"AWS training job {job_id} completed successfully")
                else:
                    job["status"] = "failed"
                    job["error"] = f"AWS launch script failed with code {process.returncode}"
                    logger.error(f"AWS training job {job_id} failed")

                job["end_time"] = datetime.utcnow()
                save_training_metadata(job_id)
            break

        # Retrieve logs from EC2 instance via SSH
        try:
            ssh_cmd = [
                "ssh",
                "-i", key_path,
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=5",
                f"ubuntu@{instance_ip}",
                "tail -50 /home/ubuntu/trading-ml/training.log 2>/dev/null || echo 'Log not ready'"
            ]

            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0 and "Log not ready" not in result.stdout:
                # Parse logs for metrics
                metrics = parse_training_log_from_text(result.stdout)

                with training_lock:
                    job["current_epoch"] = metrics["current_epoch"]
                    job["total_epochs"] = metrics["total_epochs"] or job["total_epochs"]
                    job["current_loss"] = metrics["train_loss"]
                    job["current_val_loss"] = metrics["val_loss"]
                    job["current_sharpe"] = metrics["val_sharpe"]

                    if job["total_epochs"] > 0:
                        job["progress_pct"] = (metrics["current_epoch"] / job["total_epochs"]) * 100.0

        except subprocess.TimeoutExpired:
            logger.warning(f"SSH timeout for job {job_id}")
        except Exception as e:
            logger.error(f"Error monitoring AWS training job {job_id}: {e}")

        # Wait before next check
        threading.Event().wait(10)


# ============================================================================
# REMOTE SERVER TRAINING HELPER FUNCTIONS
# ============================================================================

def launch_remote_training(job_id: str, config: str, remote_host: str, remote_user: str, device: str, debug_mode: bool) -> Dict:
    """Launch training on remote server via SSH."""

    # Local paths
    project_root = Path(__file__).parent.parent
    config_path = project_root / "ai" / "configs" / config

    # Remote paths
    remote_work_dir = f"/tmp/training_{job_id}"
    remote_log_path = f"{remote_work_dir}/training.log"

    # Local log file
    log_file = Path(f"/tmp/training_remote_{job_id}.log")

    try:
        # Create a launch script that will:
        # 1. Create remote working directory
        # 2. Transfer necessary files (config, training scripts, requirements)
        # 3. Setup Python environment if needed
        # 4. Launch training in background

        logger.info(f"Setting up remote training on {remote_user}@{remote_host}")

        # SSH key path (to bypass Tailscale SSH)
        ssh_key = os.path.expanduser("~/.ssh/id_rsa")
        ssh_base_args = [
            "-i", ssh_key,
            "-o", "StrictHostKeyChecking=no",
            "-o", "PreferredAuthentications=publickey"
        ]

        # Step 1: Create remote directory
        ssh_cmd = ["ssh"] + ssh_base_args + [
            f"{remote_user}@{remote_host}",
            f"mkdir -p {remote_work_dir}"
        ]
        subprocess.run(ssh_cmd, check=True, timeout=10)

        # Step 2: Transfer the entire ai directory (configs, train.py, etc.)
        logger.info(f"Transferring training files to remote server...")
        rsync_cmd = [
            "rsync",
            "-avz",
            "-e", f"ssh -i {ssh_key} -o StrictHostKeyChecking=no -o PreferredAuthentications=publickey",
            "--exclude", "__pycache__",
            "--exclude", "*.pyc",
            "--exclude", ".git",
            "--exclude", "checkpoints*",
            "--exclude", "datasets",
            str(project_root / "ai") + "/",
            f"{remote_user}@{remote_host}:{remote_work_dir}/ai/"
        ]
        subprocess.run(rsync_cmd, check=True, timeout=120)

        # Step 3: Build and execute training command on remote server
        debug_flag = "--debug_mode" if debug_mode else ""
        remote_train_cmd = f"""
cd {remote_work_dir}/ai && \
nohup python train.py \
    --config configs/{config} \
    --device {device} \
    {debug_flag} \
    > {remote_log_path} 2>&1 &
echo $!
"""

        logger.info(f"Starting training on remote server...")
        ssh_launch = ["ssh"] + ssh_base_args + [
            f"{remote_user}@{remote_host}",
            remote_train_cmd
        ]

        result = subprocess.run(
            ssh_launch,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            raise Exception(f"Failed to launch remote training: {result.stderr}")

        # Get the remote process PID
        remote_pid = result.stdout.strip().split('\n')[-1]
        logger.info(f"Remote training started with PID {remote_pid}")

        # Write initial log
        with open(log_file, 'w') as f:
            f.write(f"Remote training launched on {remote_host}\n")
            f.write(f"Remote work directory: {remote_work_dir}\n")
            f.write(f"Remote PID: {remote_pid}\n")
            f.write(f"Config: {config}\n")
            f.write(f"Device: {device}\n\n")

        return {
            "log_file": str(log_file),
            "remote_log_path": remote_log_path,
            "remote_work_dir": remote_work_dir,
            "remote_pid": remote_pid,
            "is_remote": True
        }

    except Exception as e:
        logger.error(f"Error launching remote training: {e}")
        raise


def monitor_remote_training(job_id: str):
    """Background thread to monitor remote training job."""
    with training_lock:
        if job_id not in training_jobs:
            return
        job = training_jobs[job_id]

    remote_host = job["remote_host"]
    remote_user = job["remote_user"]
    remote_log_path = job["remote_log_path"]
    remote_work_dir = job["remote_work_dir"]

    # Update status to running
    with training_lock:
        job["status"] = "running"

    logger.info(f"Monitoring remote training job {job_id} on {remote_host}")

    # SSH key path (to bypass Tailscale SSH)
    ssh_key = os.path.expanduser("~/.ssh/id_rsa")

    consecutive_errors = 0
    max_consecutive_errors = 5

    while True:
        try:
            # Fetch logs from remote server
            ssh_cmd = [
                "ssh",
                "-i", ssh_key,
                "-o", "StrictHostKeyChecking=no",
                "-o", "PreferredAuthentications=publickey",
                "-o", "ConnectTimeout=5",
                f"{remote_user}@{remote_host}",
                f"tail -50 {remote_log_path} 2>/dev/null || echo 'Log not ready'"
            ]

            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0 and "Log not ready" not in result.stdout:
                consecutive_errors = 0  # Reset error counter

                # Parse logs for metrics
                metrics = parse_training_log_from_text(result.stdout)

                with training_lock:
                    job["current_epoch"] = metrics["current_epoch"]
                    job["total_epochs"] = metrics["total_epochs"] or job["total_epochs"]
                    job["current_loss"] = metrics["train_loss"]
                    job["current_val_loss"] = metrics["val_loss"]
                    job["current_sharpe"] = metrics["val_sharpe"]

                    if job["total_epochs"] > 0:
                        job["progress_pct"] = (metrics["current_epoch"] / job["total_epochs"]) * 100.0

                # Check if training is complete by looking for completion markers
                if "Training completed" in result.stdout or "All epochs completed" in result.stdout:
                    logger.info(f"Remote training job {job_id} completed!")

                    # Retrieve the trained model
                    retrieve_remote_model(job_id, job)

                    with training_lock:
                        job["status"] = "completed"
                        job["end_time"] = datetime.utcnow()
                        save_training_metadata(job_id)
                    break

            else:
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(f"Too many consecutive errors monitoring job {job_id}")
                    with training_lock:
                        job["status"] = "failed"
                        job["error"] = "Lost connection to remote server"
                        job["end_time"] = datetime.utcnow()
                    break

        except subprocess.TimeoutExpired:
            logger.warning(f"SSH timeout for remote job {job_id}")
            consecutive_errors += 1
        except Exception as e:
            logger.error(f"Error monitoring remote training job {job_id}: {e}")
            consecutive_errors += 1

        if consecutive_errors >= max_consecutive_errors:
            with training_lock:
                job["status"] = "failed"
                job["error"] = f"Monitoring failed after {max_consecutive_errors} consecutive errors"
                job["end_time"] = datetime.utcnow()
            break

        # Wait before next check
        threading.Event().wait(10)


def retrieve_remote_model(job_id: str, job: Dict):
    """Retrieve trained model from remote server using scp."""
    remote_host = job["remote_host"]
    remote_user = job["remote_user"]
    remote_work_dir = job["remote_work_dir"]

    # Local checkpoint directory
    local_checkpoint_dir = Path(__file__).parent.parent / "ai" / "checkpoints_light"
    local_checkpoint_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Retrieving model from remote server for job {job_id}")

    # SSH key path (to bypass Tailscale SSH)
    ssh_key = os.path.expanduser("~/.ssh/id_rsa")

    try:
        # Find the latest checkpoint on remote server
        ssh_find = [
            "ssh",
            "-i", ssh_key,
            "-o", "StrictHostKeyChecking=no",
            "-o", "PreferredAuthentications=publickey",
            f"{remote_user}@{remote_host}",
            f"ls -t {remote_work_dir}/ai/checkpoints_light/*.pt 2>/dev/null | head -1"
        ]

        result = subprocess.run(
            ssh_find,
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0 or not result.stdout.strip():
            logger.warning(f"No model file found on remote server for job {job_id}")
            return

        remote_model_path = result.stdout.strip()
        model_filename = Path(remote_model_path).name
        local_model_path = local_checkpoint_dir / model_filename

        # Use scp to retrieve the model
        logger.info(f"Downloading {model_filename} from remote server...")
        scp_cmd = [
            "scp",
            "-i", ssh_key,
            "-o", "StrictHostKeyChecking=no",
            "-o", "PreferredAuthentications=publickey",
            f"{remote_user}@{remote_host}:{remote_model_path}",
            str(local_model_path)
        ]

        subprocess.run(scp_cmd, check=True, timeout=120)

        logger.info(f"Successfully retrieved model: {local_model_path}")

        with training_lock:
            job["model_path"] = str(local_model_path)
            job["model_filename"] = model_filename

        # Cleanup remote directory (optional)
        cleanup_cmd = [
            "ssh",
            "-i", ssh_key,
            "-o", "StrictHostKeyChecking=no",
            "-o", "PreferredAuthentications=publickey",
            f"{remote_user}@{remote_host}",
            f"rm -rf {remote_work_dir}"
        ]
        subprocess.run(cleanup_cmd, timeout=10)
        logger.info(f"Cleaned up remote directory: {remote_work_dir}")

    except Exception as e:
        logger.error(f"Error retrieving model from remote server: {e}")
        with training_lock:
            job["error"] = f"Model retrieval failed: {str(e)}"


def _portfolio_collection():
    try:
        coll = get_db()[PORTFOLIO_COLLECTION]
        coll.create_index("updated_at")
        return coll
    except Exception as exc:
        logger.warning(f"MongoDB portfolio unavailable: {exc}")
        return None


def _default_portfolio_state():
    now = datetime.utcnow()
    return {
        "_id": PORTFOLIO_DOC_ID,
        "initial_capital": PORTFOLIO_INITIAL_CAPITAL,
        "cash": PORTFOLIO_INITIAL_CAPITAL,
        "positions": [],
        "trades": [],
        "history": [],
        "updated_at": now,
    }


def _load_portfolio_state():
    coll = _portfolio_collection()
    if not coll:
        return _default_portfolio_state()

    try:
        state = coll.find_one({"_id": PORTFOLIO_DOC_ID})
        if not state:
            state = _default_portfolio_state()
            coll.insert_one(state)
        return state
    except PyMongoError as exc:
        logger.error(f"Mongo load portfolio failed: {exc}")
        return _default_portfolio_state()


def _calculate_stats(state: Dict):
    positions = state.get("positions", [])
    cash = float(state.get("cash", 0))
    initial_capital = float(state.get("initial_capital", PORTFOLIO_INITIAL_CAPITAL))

    invested = sum(
        float(pos.get("quantity", 0)) * float(pos.get("current_price", pos.get("entry_price", 0)))
        for pos in positions
    )
    total_value = cash + invested
    total_pnl = total_value - initial_capital
    total_pnl_percent = (total_pnl / initial_capital * 100) if initial_capital else 0

    return {
        "total_value": total_value,
        "cash": cash,
        "invested": invested,
        "total_pnl": total_pnl,
        "total_pnl_percent": total_pnl_percent,
    }


def _append_history(state: Dict):
    stats = _calculate_stats(state)
    history = state.get("history", [])
    history.append({
        "timestamp": datetime.utcnow(),
        "total_value": stats["total_value"],
        "cash": stats["cash"],
        "invested": stats["invested"],
        "pnl": stats["total_pnl"],
        "pnl_percent": stats["total_pnl_percent"],
    })
    state["history"] = history[-500:]


def _persist_portfolio_state(state: Dict):
    coll = _portfolio_collection()
    state["updated_at"] = datetime.utcnow()

    if not coll:
        return state
    try:
        coll.update_one({"_id": PORTFOLIO_DOC_ID}, {"$set": state}, upsert=True)
    except PyMongoError as exc:
        logger.error(f"Mongo save portfolio failed: {exc}")
    return state


def _format_state_for_response(state: Dict):
    def _iso(dt):
        return dt.isoformat() if isinstance(dt, datetime) else dt

    positions = []
    for pos in state.get("positions", []):
        entry_time = pos.get("entry_time") or datetime.utcnow()
        current_price = float(pos.get("current_price", pos.get("entry_price", 0)))
        quantity = float(pos.get("quantity", 0))
        entry_price = float(pos.get("entry_price", 0))
        positions.append({
            "symbol": pos.get("symbol"),
            "quantity": quantity,
            "entry_price": entry_price,
            "current_price": current_price,
            "value": current_price * quantity,
            "pnl": (current_price - entry_price) * quantity,
            "pnl_percent": ((current_price - entry_price) / entry_price * 100) if entry_price else 0,
            "entry_time": _iso(entry_time),
        })

    trades = []
    for t in state.get("trades", []):
        trades.append({
            "id": t.get("id"),
            "timestamp": _iso(t.get("timestamp")),
            "symbol": t.get("symbol"),
            "action": t.get("action"),
            "quantity": float(t.get("quantity", 0)),
            "price": float(t.get("price", 0)),
            "total": float(t.get("total", 0)),
            "reason": t.get("reason"),
            "confidence": float(t.get("confidence", 0)),
        })

    history = []
    for h in state.get("history", []):
        history.append({
            "timestamp": _iso(h.get("timestamp")),
            "total_value": float(h.get("total_value", 0)),
            "cash": float(h.get("cash", 0)),
            "invested": float(h.get("invested", 0)),
            "pnl": float(h.get("pnl", 0)),
            "pnl_percent": float(h.get("pnl_percent", 0)),
        })

    stats = _calculate_stats(state)

    return {
        "initial_capital": float(state.get("initial_capital", PORTFOLIO_INITIAL_CAPITAL)),
        "cash": stats["cash"],
        "positions": positions,
        "trades": sorted(trades, key=lambda x: x["timestamp"], reverse=True)[:200],
        "history": history,
        "stats": stats,
        "updated_at": _iso(state.get("updated_at")),
    }


def _apply_trade(state: Dict, payload: TradeRequest):
    symbol = payload.symbol.upper()
    price = float(payload.price)
    action = payload.action.upper()
    confidence = float(payload.confidence or 0)
    reason = payload.reason or "AI signal"
    now = datetime.utcnow()

    positions = state.get("positions", [])
    cash = float(state.get("cash", 0))

    if action == "BUY":
        investment = cash * 0.1
        if investment < 10 or investment > cash or price <= 0:
            return state  # Not enough cash or invalid price
        quantity = investment / price

        existing = next((p for p in positions if p.get("symbol") == symbol), None)
        if existing:
            old_qty = float(existing.get("quantity", 0))
            new_qty = old_qty + quantity
            entry_price = (
                float(existing.get("entry_price", 0)) * old_qty + price * quantity
            ) / new_qty
            existing.update({
                "quantity": new_qty,
                "entry_price": entry_price,
                "current_price": price,
                "entry_time": existing.get("entry_time", now),
            })
        else:
            positions.append({
                "symbol": symbol,
                "quantity": quantity,
                "entry_price": price,
                "current_price": price,
                "entry_time": now,
            })

        cash -= investment

        trade_total = quantity * price
        state["trades"] = [{
            "id": f"{int(now.timestamp() * 1000)}-{symbol}",
            "timestamp": now,
            "symbol": symbol,
            "action": "BUY",
            "quantity": quantity,
            "price": price,
            "total": trade_total,
            "reason": reason,
            "confidence": confidence,
        }] + state.get("trades", [])[:199]

    elif action == "SELL":
        existing = next((p for p in positions if p.get("symbol") == symbol), None)
        if not existing:
            return state  # Nothing to sell
        quantity = float(existing.get("quantity", 0))
        trade_total = quantity * price
        cash += trade_total
        positions = [p for p in positions if p.get("symbol") != symbol]

        state["trades"] = [{
            "id": f"{int(now.timestamp() * 1000)}-{symbol}",
            "timestamp": now,
            "symbol": symbol,
            "action": "SELL",
            "quantity": quantity,
            "price": price,
            "total": trade_total,
            "reason": reason,
            "confidence": confidence,
        }] + state.get("trades", [])[:199]

    # Refresh state
    state["positions"] = positions
    state["cash"] = cash

    # Update history
    _append_history(state)
    _persist_portfolio_state(state)
    return state


@app.get("/portfolio/state")
async def get_portfolio_state():
    state = _load_portfolio_state()
    return _format_state_for_response(state)


@app.get("/portfolio/history")
async def get_portfolio_history():
    state = _load_portfolio_state()
    return {"history": _format_state_for_response(state)["history"]}


@app.post("/portfolio/trade")
async def post_portfolio_trade(payload: TradeRequest):
    state = _load_portfolio_state()
    state = _apply_trade(state, payload)
    return _format_state_for_response(state)


@app.post("/portfolio/reset")
async def reset_portfolio():
    state = _default_portfolio_state()
    _append_history(state)
    _persist_portfolio_state(state)
    return _format_state_for_response(state)


@app.get("/dataset/signals")
async def get_signals():
    """Récupérer les signaux alpha détectés."""
    dataset_path = get_latest_dataset_path()
    signals_file = dataset_path / "alpha_signals_report.json"

    if not signals_file.exists():
        return {"signals": [], "count": 0}

    with open(signals_file, 'r') as f:
        signals = json.load(f)

    # Statistiques sur les signaux
    df = pd.DataFrame(signals)

    stats = {
        "total": len(signals),
        "by_direction": df['direction'].value_counts().to_dict() if 'direction' in df.columns else {},
        "by_strength": df['strength'].value_counts().to_dict() if 'strength' in df.columns else {},
        "by_asset": df['asset'].value_counts().to_dict() if 'asset' in df.columns else {},
        "by_type": df['signal_type'].value_counts().to_dict() if 'signal_type' in df.columns else {},
    }

    return {
        "signals": signals,
        "stats": stats
    }

@app.get("/dataset/ohlcv/{symbol:path}")
async def get_ohlcv(symbol: str, limit: int = 1000):
    """Récupérer les données OHLCV pour un symbol.
    Supporte BTC/USDT et BTCUSDT formats."""

    # Essayer d'abord avec les données historiques (format BTC/USDT)
    try:
        # Normaliser le symbole: enlever le slash si présent
        symbol_normalized = symbol.replace('/', '_').upper()
        historical_dir = Path("datasets/historical_crypto")

        # Chercher le fichier historique
        pattern = f"{symbol_normalized}_1h_*.parquet"
        files = list(historical_dir.glob(pattern))

        if files:
            # Utiliser le fichier le plus récent
            latest_file = sorted(files, reverse=True)[0]
            df = pd.read_parquet(latest_file)

            # Appliquer la limite
            df = df.tail(limit)

            # S'assurer que timestamp est en datetime
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.sort_values('timestamp')

            return {
                "symbol": symbol,
                "data": df.to_dict(orient='records'),
                "count": len(df)
            }
    except Exception as e:
        logger.warning(f"Failed to load from historical data: {e}")

    # Fallback: essayer avec les données alpha trading
    dataset_path = get_latest_dataset_path()
    ohlcv_file = dataset_path / "binance_ohlcv.parquet"

    if not ohlcv_file.exists():
        raise HTTPException(status_code=404, detail="OHLCV data not found")

    df = pd.read_parquet(ohlcv_file)

    # Essayer avec et sans slash
    df_symbol = df[df['symbol'] == symbol].tail(limit)
    if len(df_symbol) == 0:
        # Essayer sans slash
        symbol_no_slash = symbol.replace('/', '')
        df_symbol = df[df['symbol'] == symbol_no_slash].tail(limit)

    if len(df_symbol) == 0:
        available_symbols = df['symbol'].unique().tolist()
        raise HTTPException(
            status_code=404,
            detail=f"Symbol {symbol} not found. Available: {available_symbols[:10]}"
        )

    # Convertir en format pour ECharts
    df_symbol['timestamp'] = pd.to_datetime(df_symbol['timestamp'])
    df_symbol = df_symbol.sort_values('timestamp')

    return {
        "symbol": symbol,
        "data": df_symbol.to_dict(orient='records'),
        "count": len(df_symbol)
    }

@app.get("/dataset/funding-rates")
async def get_funding_rates():
    """Récupérer les funding rates."""
    dataset_path = get_latest_dataset_path()
    funding_file = dataset_path / "funding_rates.parquet"

    if not funding_file.exists():
        raise HTTPException(status_code=404, detail="Funding rates not found")

    df = pd.read_parquet(funding_file)

    # Grouper par symbol et prendre les dernières valeurs
    latest_by_symbol = df.groupby('symbol').last().reset_index()

    return {
        "data": latest_by_symbol.to_dict(orient='records'),
        "count": len(latest_by_symbol)
    }

@app.get("/dataset/fear-greed")
async def get_fear_greed():
    """Récupérer le Fear & Greed Index."""
    dataset_path = get_latest_dataset_path()
    fg_file = dataset_path / "fear_greed_index.parquet"

    if not fg_file.exists():
        raise HTTPException(status_code=404, detail="Fear & Greed data not found")

    df = pd.read_parquet(fg_file)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp')

    return {
        "data": df.to_dict(orient='records'),
        "latest": df.iloc[-1].to_dict() if len(df) > 0 else None,
        "count": len(df)
    }

@app.get("/dataset/sentiment")
async def get_sentiment():
    """Récupérer les données de sentiment Reddit."""
    dataset_path = get_latest_dataset_path()
    reddit_file = dataset_path / "reddit_sentiment.parquet"

    if not reddit_file.exists():
        raise HTTPException(status_code=404, detail="Sentiment data not found")

    df = pd.read_parquet(reddit_file)

    # Top posts par engagement
    df['engagement'] = df['score'] + df['num_comments']
    top_posts = df.nlargest(20, 'engagement')

    # Statistiques par subreddit
    by_subreddit = df.groupby('subreddit').agg({
        'score': 'sum',
        'num_comments': 'sum',
        'title': 'count'
    }).reset_index()
    by_subreddit.columns = ['subreddit', 'total_score', 'total_comments', 'post_count']

    return {
        "top_posts": top_posts.to_dict(orient='records'),
        "by_subreddit": by_subreddit.to_dict(orient='records'),
        "total_posts": len(df)
    }

@app.get("/dataset/macro")
async def get_macro():
    """Récupérer les données macroéconomiques."""
    dataset_path = get_latest_dataset_path()

    data = {}

    # FRED data
    fred_file = dataset_path / "fred_economic.parquet"
    if fred_file.exists():
        df_fred = pd.read_parquet(fred_file)
        data['fred'] = df_fred.groupby('series').tail(30).to_dict(orient='records')

    # Stock indices
    indices_file = dataset_path / "stock_indices.parquet"
    if indices_file.exists():
        df_indices = pd.read_parquet(indices_file)
        data['indices'] = df_indices.to_dict(orient='records')

    return data

@app.get("/dataset/derivatives")
async def get_derivatives():
    """Récupérer les données dérivés."""
    dataset_path = get_latest_dataset_path()

    data = {}

    # Funding rates
    funding_file = dataset_path / "funding_rates.parquet"
    if funding_file.exists():
        df = pd.read_parquet(funding_file)
        data['funding_rates'] = df.groupby('symbol').last().reset_index().to_dict(orient='records')

    # Open interest
    oi_file = dataset_path / "open_interest.parquet"
    if oi_file.exists():
        df = pd.read_parquet(oi_file)
        data['open_interest'] = df.to_dict(orient='records')

    # Long/Short ratio
    ls_file = dataset_path / "long_short_ratio.parquet"
    if ls_file.exists():
        df = pd.read_parquet(ls_file)
        data['long_short_ratio'] = df.groupby('symbol').last().reset_index().to_dict(orient='records')

    return data


# ============================================================================
# HISTORICAL DATA ENDPOINTS (crypto OHLCV)
# ============================================================================

HISTORICAL_DATA_DIR = Path("datasets/historical_crypto")


def load_historical_data(symbol: str, limit: Optional[int] = None, interval: str = "1h") -> Optional[pd.DataFrame]:
    """
    Charger les données historiques d'une crypto.
    Essaie MongoDB d'abord, puis bascule sur les fichiers Parquet locaux.
    """
    norm_symbol = normalize_symbol(symbol)

    # Mongo (si les données ont été ingérées)
    df = fetch_historical_from_mongo(norm_symbol, limit=limit, interval=interval)
    if df is not None and not df.empty:
        return df

    # Fichiers locaux en fallback
    safe_symbol = norm_symbol.replace("/", "_")
    pattern = f"{safe_symbol}_{interval}_*.parquet"
    files = list(HISTORICAL_DATA_DIR.glob(pattern))

    if not files:
        return None

    latest_file = max(files, key=lambda f: f.stat().st_mtime)
    df = pd.read_parquet(latest_file)
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    if limit:
        df = df.tail(limit)

    return df


def _build_historical_response(symbol: str, limit: Optional[int], interval: Optional[str]):
    interval = interval or "1h"
    df = load_historical_data(symbol, limit=limit, interval=interval)

    print(df)
    if df is None or df.empty:
        raise HTTPException(status_code=404, detail=f"Crypto {symbol} not found")

    df = df.sort_values("timestamp")
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")

    return {
        "success": True,
        "symbol": normalize_symbol(symbol),
        "interval": interval,
        "count": len(df),
        "data": df.to_dict("records"),
    }


@app.get("/api/historical/{symbol:path}")
async def get_historical_symbol(symbol: str, limit: Optional[int] = None, interval: Optional[str] = "1h"):
    """Données historiques via paramètre dans le path: /api/historical/BTC/USDT."""
    return _build_historical_response(symbol, limit, interval)


@app.get("/api/historical/")
async def get_historical_query(symbol: str, limit: Optional[int] = None, interval: Optional[str] = "1h"):
    """Données historiques via query string: /api/historical/?symbol=BTC/USDT&limit=500."""
    return _build_historical_response(symbol, limit, interval)

@app.get("/market/all-cryptos")
async def get_all_cryptos():
    """Récupérer toutes les cryptos avec leurs prix actuels et précédents."""
    try:
        dataset_path = get_latest_dataset_path()
        ohlcv_file = dataset_path / "binance_ohlcv.parquet"

        if not ohlcv_file.exists():
            return {"cryptos": [], "count": 0, "message": "No OHLCV data available"}

        df = pd.read_parquet(ohlcv_file)

        # Pour chaque symbol, récupérer les dernières valeurs
        cryptos_data = []
        for symbol in df['symbol'].unique():
            df_symbol = df[df['symbol'] == symbol].sort_values('timestamp')

            if len(df_symbol) >= 2:
                latest = df_symbol.iloc[-1]
                previous = df_symbol.iloc[-2]

                # Convertir en float pour éviter les erreurs de type
                latest_close = float(latest['close'])
                previous_close = float(previous['close'])

                # Calculer les variations
                price_change = latest_close - previous_close
                price_change_pct = (price_change / previous_close) * 100

                # Calculer 24h change (environ 24 candles de 1h)
                h24_ago_idx = max(0, len(df_symbol) - 24)
                h24_ago = df_symbol.iloc[h24_ago_idx]
                h24_ago_close = float(h24_ago['close'])
                h24_change = latest_close - h24_ago_close
                h24_change_pct = (h24_change / h24_ago_close) * 100

                crypto_info = {
                    "symbol": symbol,
                    "name": symbol.replace('USDT', ''),
                    "current_price": latest_close,
                    "previous_price": previous_close,
                    "open": float(latest['open']),
                    "high": float(latest['high']),
                    "low": float(latest['low']),
                    "volume": float(latest['volume']),
                    "price_change": price_change,
                    "price_change_pct": price_change_pct,
                    "h24_high": float(df_symbol.tail(24)['high'].astype(float).max()),
                    "h24_low": float(df_symbol.tail(24)['low'].astype(float).min()),
                    "h24_volume": float(df_symbol.tail(24)['volume'].astype(float).sum()),
                    "h24_change": h24_change,
                    "h24_change_pct": h24_change_pct,
                    "timestamp": latest['timestamp'].isoformat() if hasattr(latest['timestamp'], 'isoformat') else str(latest['timestamp']),
                    "is_positive": price_change >= 0,
                }

                cryptos_data.append(crypto_info)

        # Trier par volume 24h décroissant
        cryptos_data.sort(key=lambda x: x['h24_volume'], reverse=True)

        # Statistiques globales
        stats = {
            "total_cryptos": len(cryptos_data),
            "gainers": len([c for c in cryptos_data if c['h24_change_pct'] > 0]),
            "losers": len([c for c in cryptos_data if c['h24_change_pct'] < 0]),
            "neutral": len([c for c in cryptos_data if c['h24_change_pct'] == 0]),
            "top_gainer": max(cryptos_data, key=lambda x: x['h24_change_pct']) if cryptos_data else None,
            "top_loser": min(cryptos_data, key=lambda x: x['h24_change_pct']) if cryptos_data else None,
            "highest_volume": max(cryptos_data, key=lambda x: x['h24_volume']) if cryptos_data else None,
        }

        return {
            "cryptos": cryptos_data,
            "count": len(cryptos_data),
            "stats": stats,
            "timestamp": datetime.utcnow().isoformat()
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading crypto data: {str(e)}")

@app.get("/market/ticker")
async def get_ticker(symbol: str = "BTCUSDT"):
    """Récupérer le ticker pour un symbol spécifique."""
    try:
        dataset_path = get_latest_dataset_path()
        ohlcv_file = dataset_path / "binance_ohlcv.parquet"

        if not ohlcv_file.exists():
            raise HTTPException(status_code=404, detail="OHLCV data not found")

        df = pd.read_parquet(ohlcv_file)
        df_symbol = df[df['symbol'] == symbol].sort_values('timestamp')

        if len(df_symbol) == 0:
            raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")

        latest = df_symbol.iloc[-1]

        # Calculer 24h stats
        h24_data = df_symbol.tail(24)
        h24_change = latest['close'] - h24_data.iloc[0]['close']
        h24_change_pct = (h24_change / h24_data.iloc[0]['close']) * 100

        return {
            "symbol": symbol,
            "price": float(latest['close']),
            "priceChange24h": float(h24_change),
            "priceChangePercent24h": float(h24_change_pct),
            "high24h": float(h24_data['high'].max()),
            "low24h": float(h24_data['low'].min()),
            "volume24h": float(h24_data['volume'].sum()),
            "quoteVolume24h": float(h24_data['volume'].sum() * h24_data['close'].mean()),
            "timestamp": latest['timestamp'].isoformat() if hasattr(latest['timestamp'], 'isoformat') else str(latest['timestamp']),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/market/klines")
async def get_klines(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 500):
    """Récupérer les klines (candlestick data) depuis Binance API."""
    try:
        import requests

        # Map d'intervalles
        interval_map = {
            '1m': '1m',
            '5m': '5m',
            '15m': '15m',
            '1h': '1h',
            '4h': '4h',
            '1d': '1d'
        }

        binance_interval = interval_map.get(interval, '1h')

        # Appel à l'API Binance
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": binance_interval,
            "limit": min(limit, 1000)  # Binance max = 1000
        }

        response = requests.get(url, params=params)

        if response.status_code != 200:
            raise HTTPException(status_code=404, detail=f"Failed to fetch data for {symbol}")

        data = response.json()

        # Convertir au format attendu
        klines = []
        for candle in data:
            klines.append({
                "time": int(candle[0] / 1000),  # Convert ms to seconds
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5])
            })

        return klines

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/market/orderbook")
async def get_orderbook(symbol: str = "BTCUSDT", depth: int = 20):
    """Générer un order book simulé basé sur les données OHLCV."""
    try:
        dataset_path = get_latest_dataset_path()
        ohlcv_file = dataset_path / "binance_ohlcv.parquet"

        if not ohlcv_file.exists():
            raise HTTPException(status_code=404, detail="OHLCV data not found")

        df = pd.read_parquet(ohlcv_file)
        df_symbol = df[df['symbol'] == symbol].sort_values('timestamp')

        if len(df_symbol) == 0:
            raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")

        latest = df_symbol.iloc[-1]
        base_price = float(latest['close'])

        # Générer asks (ordres de vente)
        asks = []
        for i in range(depth):
            price = base_price + (i + 1) * (base_price * 0.0001)  # 0.01% par niveau
            quantity = (20 - i) * 0.1  # Quantité décroissante
            asks.append({
                "price": f"{price:.2f}",
                "quantity": f"{quantity:.4f}",
                "total": f"{price * quantity:.2f}"
            })

        # Générer bids (ordres d'achat)
        bids = []
        for i in range(depth):
            price = base_price - (i + 1) * (base_price * 0.0001)
            quantity = (20 - i) * 0.1
            bids.append({
                "price": f"{price:.2f}",
                "quantity": f"{quantity:.4f}",
                "total": f"{price * quantity:.2f}"
            })

        return {
            "asks": asks,
            "bids": bids
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/market/trades")
async def get_recent_trades(symbol: str = "BTCUSDT", limit: int = 50):
    """Générer des trades récents simulés."""
    try:
        dataset_path = get_latest_dataset_path()
        ohlcv_file = dataset_path / "binance_ohlcv.parquet"

        if not ohlcv_file.exists():
            raise HTTPException(status_code=404, detail="OHLCV data not found")

        df = pd.read_parquet(ohlcv_file)
        df_symbol = df[df['symbol'] == symbol].sort_values('timestamp').tail(10)

        if len(df_symbol) == 0:
            raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")

        import random
        trades = []

        for _, row in df_symbol.iterrows():
            # Générer quelques trades par candle
            for i in range(5):
                price_range = float(row['high']) - float(row['low'])
                price = float(row['low']) + random.random() * price_range
                quantity = random.uniform(0.01, 2.0)

                timestamp = row['timestamp']
                if hasattr(timestamp, 'timestamp'):
                    time_ms = int(timestamp.timestamp() * 1000) + i * 1000
                else:
                    time_ms = int(pd.Timestamp(timestamp).timestamp() * 1000) + i * 1000

                trades.append({
                    "id": time_ms,
                    "price": f"{price:.2f}",
                    "quantity": f"{quantity:.4f}",
                    "time": time_ms,
                    "isBuyerMaker": random.choice([True, False])
                })

        # Trier par temps et limiter
        trades.sort(key=lambda x: x['time'], reverse=True)
        return trades[:limit]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    try:
        dataset_path = get_latest_dataset_path()
        return {
            "status": "healthy",
            "dataset": dataset_path.name,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }

# ============================================================================
# REAL-TIME PIPELINE ENDPOINTS
# ============================================================================

# Import du connector (optionnel)
try:
    from pipeline_api_connector import pipeline_connector
    PIPELINE_AVAILABLE = True
except ImportError:
    PIPELINE_AVAILABLE = False
    logger.warning("Pipeline connector not available - prediction endpoints will be disabled")

@app.post("/pipeline/start")
async def start_pipeline(config: Optional[Dict] = None):
    """Démarrer la pipeline temps réel."""
    if not PIPELINE_AVAILABLE:
        raise HTTPException(status_code=503, detail="Pipeline connector not available")
    try:
        result = await pipeline_connector.start_pipeline(config)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/pipeline/stop")
async def stop_pipeline():
    """Arrêter la pipeline."""
    if not PIPELINE_AVAILABLE:
        raise HTTPException(status_code=503, detail="Pipeline connector not available")
    try:
        result = await pipeline_connector.stop_pipeline()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/pipeline/status")
async def get_pipeline_status():
    """Obtenir le statut et les stats de la pipeline."""
    if not PIPELINE_AVAILABLE:
        return {"status": "unavailable", "message": "Pipeline connector not configured"}
    try:
        stats = pipeline_connector.get_stats()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/pipeline/predictions")
async def get_all_predictions():
    """Obtenir toutes les prédictions actuelles."""
    if not PIPELINE_AVAILABLE:
        return {"count": 0, "predictions": [], "message": "Pipeline not available"}
    try:
        predictions = pipeline_connector.get_predictions()
        return {
            "count": len(predictions),
            "predictions": predictions,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/pipeline/prediction/{symbol}")
async def get_prediction(symbol: str):
    """Obtenir la prédiction pour un symbole spécifique."""
    if not PIPELINE_AVAILABLE:
        raise HTTPException(status_code=503, detail="Pipeline connector not available")
    try:
        prediction = pipeline_connector.get_prediction(symbol.upper())
        return prediction
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/pipeline/features/{symbol}")
async def get_features(symbol: str):
    """Obtenir les features calculées pour un symbole."""
    if not PIPELINE_AVAILABLE:
        raise HTTPException(status_code=503, detail="Pipeline connector not available")
    try:
        features = pipeline_connector.get_features(symbol.upper())
        if not features:
            raise HTTPException(status_code=404, detail=f"No features available for {symbol}")
        return features
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/pipeline/symbols")
async def get_active_symbols():
    """Obtenir la liste des symboles actifs."""
    if not PIPELINE_AVAILABLE:
        return {"count": 0, "symbols": []}
    try:
        symbols = pipeline_connector.get_active_symbols()
        return {
            "count": len(symbols),
            "symbols": symbols
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/pipeline/predictions/future/{symbol}")
async def get_future_predictions(symbol: str, minutes: int = 5):
    """Obtenir les prédictions pour les N prochaines minutes."""
    if not PIPELINE_AVAILABLE:
        # Générer des prédictions basiques si pipeline non disponible
        current_time = datetime.utcnow()
        predictions = []

        # Prix de base estimé (on pourrait le récupérer d'une API publique)
        base_prices = {
            "BTCUSDT": 42000,
            "ETHUSDT": 2200,
            "BNBUSDT": 300,
            "SOLUSDT": 100,
            "XRPUSDT": 0.60
        }
        base_price = base_prices.get(symbol.upper(), 100)

        for i in range(1, minutes + 1):
            # Simuler une petite variation (-0.5% à +0.5% par minute)
            variation = (random.random() - 0.5) * 0.01
            predicted_price = base_price * (1 + variation * i)
            confidence = 0.5 + random.random() * 0.3  # 50-80% confiance

            predictions.append({
                "minute": i,
                "timestamp": (current_time + timedelta(minutes=i)).isoformat(),
                "predicted_price": round(predicted_price, 6),
                "confidence": round(confidence, 2),
                "change_pct": round(variation * i * 100, 3)
            })

        return {
            "symbol": symbol.upper(),
            "current_time": current_time.isoformat(),
            "predictions": predictions,
            "source": "simulated"
        }

    try:
        # Obtenir la prédiction actuelle
        current_pred = pipeline_connector.get_prediction(symbol.upper())
        current_time = datetime.utcnow()
        predictions = []

        # Estimer le prix actuel
        current_price = current_pred.get("price", 0)
        confidence = current_pred.get("confidence", 0.5)

        # Générer des prédictions pour les prochaines minutes
        for i in range(1, minutes + 1):
            # Utiliser une variation basée sur la tendance actuelle
            trend = (random.random() - 0.5) * 0.008  # -0.4% à +0.4% par minute
            predicted_price = current_price * (1 + trend * i)

            predictions.append({
                "minute": i,
                "timestamp": (current_time + timedelta(minutes=i)).isoformat(),
                "predicted_price": round(predicted_price, 6),
                "confidence": round(confidence * (1 - i * 0.05), 2),  # Confiance diminue avec le temps
                "change_pct": round(trend * i * 100, 3)
            })

        return {
            "symbol": symbol.upper(),
            "current_time": current_time.isoformat(),
            "current_price": current_price,
            "predictions": predictions,
            "source": "pipeline"
        }
    except Exception as e:
        logger.error(f"Error generating future predictions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# TRAINING MANAGEMENT ENDPOINTS
# ============================================================================

@app.get("/training/configs")
async def get_training_configs():
    """Get list of available training configurations."""
    configs = get_available_configs()
    return {
        "success": True,
        "configs": configs,
        "count": len(configs)
    }

@app.post("/training/start")
async def start_training(request: TrainingStartRequest):
    """Start a new training job - AWS, Remote Server, or Local."""
    try:
        # Validate config file exists
        config_path = Path(__file__).parent.parent / "ai" / "configs" / request.config
        if not config_path.exists():
            raise HTTPException(status_code=404, detail=f"Config file {request.config} not found")

        # Generate unique job ID
        job_id = f"train_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        if request.training_location == "aws":
            # Launch on AWS EC2
            logger.info(f"Launching AWS training job {job_id} on {request.instance_type}")

            aws_result = launch_aws_training(
                job_id,
                request.config,
                request.instance_type,
                request.aws_region,
                request.debug_mode
            )

            # Create job entry for AWS
            job = {
                "job_id": job_id,
                "status": "launching",  # Special status for AWS
                "config_path": str(config_path),
                "device": "cuda",  # Always GPU on AWS
                "debug_mode": request.debug_mode,
                "is_aws": True,
                "instance_type": request.instance_type,
                "aws_region": request.aws_region,
                "process": aws_result["process"],
                "start_time": datetime.utcnow(),
                "end_time": None,
                "current_epoch": 0,
                "total_epochs": 50,
                "progress_pct": 0.0,
                "current_loss": 0.0,
                "current_val_loss": 0.0,
                "current_sharpe": 0.0,
                "log_file": aws_result["log_file"],
                "error": None,
                "aws_instance_id": None,  # Will be filled by monitor
                "aws_public_ip": None,
                "aws_s3_path": None
            }

            with training_lock:
                training_jobs[job_id] = job

            # Start AWS monitoring thread
            monitor_thread = threading.Thread(target=monitor_aws_training, args=(job_id,), daemon=True)
            monitor_thread.start()

            logger.info(f"Started AWS training job {job_id} with config {request.config}")

            return {
                "success": True,
                "job_id": job_id,
                "status": "launching",
                "is_aws": True,
                "is_remote": False,
                "instance_type": request.instance_type,
                "message": f"Training launching on AWS EC2 ({request.instance_type})"
            }

        elif request.training_location == "remote":
            # Launch on remote server via SSH
            logger.info(f"Launching remote training job {job_id} on {request.remote_host}")

            remote_result = launch_remote_training(
                job_id,
                request.config,
                request.remote_host,
                request.remote_user,
                request.device,
                request.debug_mode
            )

            # Create job entry for remote server
            job = {
                "job_id": job_id,
                "status": "launching",
                "config_path": str(config_path),
                "device": request.device,
                "debug_mode": request.debug_mode,
                "is_aws": False,
                "is_remote": True,
                "remote_host": request.remote_host,
                "remote_user": request.remote_user,
                "process": remote_result.get("process"),
                "start_time": datetime.utcnow(),
                "end_time": None,
                "current_epoch": 0,
                "total_epochs": 50,
                "progress_pct": 0.0,
                "current_loss": 0.0,
                "current_val_loss": 0.0,
                "current_sharpe": 0.0,
                "log_file": remote_result["log_file"],
                "remote_log_path": remote_result["remote_log_path"],
                "remote_work_dir": remote_result["remote_work_dir"],
                "error": None
            }

            with training_lock:
                training_jobs[job_id] = job

            # Start remote monitoring thread
            monitor_thread = threading.Thread(target=monitor_remote_training, args=(job_id,), daemon=True)
            monitor_thread.start()

            logger.info(f"Started remote training job {job_id} on {request.remote_host}")

            return {
                "success": True,
                "job_id": job_id,
                "status": "launching",
                "is_aws": False,
                "is_remote": True,
                "remote_host": request.remote_host,
                "message": f"Training launching on remote server ({request.remote_host})"
            }

        else:
            # Launch locally (original code)
            # Create log file
            log_dir = Path("/tmp")
            log_file = log_dir / f"training_{job_id}.log"

            # Build training command
            train_script = Path(__file__).parent.parent / "ai" / "train.py"
            cmd = [
                "python",
                str(train_script),
                "--config", str(config_path),
                "--device", request.device,
            ]

            if request.debug_mode:
                cmd.append("--debug_mode")

            # Start subprocess
            process = subprocess.Popen(
                cmd,
                stdout=open(log_file, 'w'),
                stderr=subprocess.STDOUT,
                cwd=str(Path(__file__).parent.parent)
            )

            # Create job entry
            job = {
                "job_id": job_id,
                "status": "running",
                "config_path": str(config_path),
                "device": request.device,
                "debug_mode": request.debug_mode,
                "is_aws": False,
                "process": process,
                "start_time": datetime.utcnow(),
                "end_time": None,
                "current_epoch": 0,
                "total_epochs": 50,
                "progress_pct": 0.0,
                "current_loss": 0.0,
                "current_val_loss": 0.0,
                "current_sharpe": 0.0,
                "log_file": str(log_file),
                "error": None
            }

            with training_lock:
                training_jobs[job_id] = job

            # Start monitoring thread
            monitor_thread = threading.Thread(target=monitor_training_process, args=(job_id,), daemon=True)
            monitor_thread.start()

            logger.info(f"Started local training job {job_id} with config {request.config}")

            return {
                "success": True,
                "job_id": job_id,
                "status": "running",
                "is_aws": False,
                "is_remote": False,
                "message": f"Training started locally with config {request.config}"
            }

    except Exception as e:
        logger.error(f"Error starting training: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/training/jobs")
async def get_all_training_jobs():
    """Get all training jobs (last 24 hours)."""
    with training_lock:
        jobs_list = []
        cutoff_time = datetime.utcnow() - timedelta(hours=24)

        for job_id, job in training_jobs.items():
            if job["start_time"] > cutoff_time:
                job_copy = job.copy()
                job_copy.pop("process", None)  # Remove non-serializable field

                # Convert datetime to ISO format
                if isinstance(job_copy.get("start_time"), datetime):
                    job_copy["start_time"] = job_copy["start_time"].isoformat()
                if isinstance(job_copy.get("end_time"), datetime):
                    job_copy["end_time"] = job_copy["end_time"].isoformat()

                jobs_list.append(job_copy)

        # Sort by start time (most recent first)
        jobs_list.sort(key=lambda x: x["start_time"], reverse=True)

    return {
        "success": True,
        "jobs": jobs_list,
        "count": len(jobs_list)
    }

@app.get("/training/status/{job_id}")
async def get_training_status(job_id: str):
    """Get status of a specific training job."""
    with training_lock:
        if job_id not in training_jobs:
            raise HTTPException(status_code=404, detail=f"Training job {job_id} not found")

        job = training_jobs[job_id].copy()
        job.pop("process", None)

        # Convert datetime to ISO format
        if isinstance(job.get("start_time"), datetime):
            job["start_time"] = job["start_time"].isoformat()
        if isinstance(job.get("end_time"), datetime):
            job["end_time"] = job["end_time"].isoformat()

    return {
        "success": True,
        "job": job
    }

@app.post("/training/stop/{job_id}")
async def stop_training(job_id: str):
    """Stop a running training job - AWS or local."""
    with training_lock:
        if job_id not in training_jobs:
            raise HTTPException(status_code=404, detail=f"Training job {job_id} not found")

        job = training_jobs[job_id]

        if job["status"] not in ["running", "launching"]:
            raise HTTPException(status_code=400, detail=f"Job {job_id} is not running (status: {job['status']})")

    try:
        if job.get("is_aws"):
            # Terminate AWS EC2 instance
            instance_id = job.get("aws_instance_id")
            aws_region = job.get("aws_region", "eu-west-3")

            if instance_id:
                logger.info(f"Terminating AWS instance {instance_id} for job {job_id}")

                # Use AWS CLI to terminate instance
                terminate_cmd = [
                    "aws", "ec2", "terminate-instances",
                    "--instance-ids", instance_id,
                    "--region", aws_region
                ]

                result = subprocess.run(
                    terminate_cmd,
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                if result.returncode == 0:
                    logger.info(f"Successfully terminated AWS instance {instance_id}")
                else:
                    logger.error(f"Failed to terminate instance: {result.stderr}")

            # Also terminate the local launch script process
            process = job.get("process")
            if process:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

            with training_lock:
                job["status"] = "stopped"
                job["end_time"] = datetime.utcnow()

            save_training_metadata(job_id)
            logger.info(f"Stopped AWS training job {job_id}")

            return {
                "success": True,
                "job_id": job_id,
                "status": "stopped",
                "message": f"Training job {job_id} stopped (AWS instance terminated)"
            }

        else:
            # Stop local training
            process = job["process"]

            # Try graceful shutdown first
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Force kill if graceful shutdown fails
                process.kill()
                process.wait()

            with training_lock:
                job["status"] = "stopped"
                job["end_time"] = datetime.utcnow()

            save_training_metadata(job_id)
            logger.info(f"Stopped local training job {job_id}")

            return {
                "success": True,
                "job_id": job_id,
                "status": "stopped",
                "message": f"Training job {job_id} stopped"
            }

    except Exception as e:
        logger.error(f"Error stopping training job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/training/logs/{job_id}")
async def get_training_logs(job_id: str, lines: int = 100):
    """Get training logs for a specific job."""
    with training_lock:
        if job_id not in training_jobs:
            raise HTTPException(status_code=404, detail=f"Training job {job_id} not found")

        log_file = training_jobs[job_id]["log_file"]

    if not Path(log_file).exists():
        return {
            "success": True,
            "job_id": job_id,
            "logs": [],
            "message": "Log file not yet created"
        }

    try:
        with open(log_file, 'r') as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

        return {
            "success": True,
            "job_id": job_id,
            "logs": [line.strip() for line in recent_lines],
            "total_lines": len(all_lines)
        }

    except Exception as e:
        logger.error(f"Error reading log file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/training/models")
async def get_model_versions():
    """Get all trained model versions with metadata."""
    checkpoints_dir = Path(__file__).parent.parent / "ai" / "checkpoints_light"

    if not checkpoints_dir.exists():
        return {
            "success": True,
            "models": [],
            "count": 0,
            "message": "Checkpoints directory not found"
        }

    models = []

    # Scan for .pt files
    for pt_file in sorted(checkpoints_dir.glob("*.pt"), key=lambda p: p.stat().st_mtime, reverse=True):
        model_info = {
            "filename": pt_file.name,
            "path": str(pt_file),
            "created_at": datetime.fromtimestamp(pt_file.stat().st_mtime).isoformat(),
            "size_mb": round(pt_file.stat().st_size / (1024 * 1024), 2),
            "metadata": {}
        }

        # Look for corresponding metadata file
        metadata_file = pt_file.parent / f"{pt_file.stem}_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, 'r') as f:
                    model_info["metadata"] = json.load(f)
            except Exception as e:
                logger.warning(f"Error loading metadata for {pt_file.name}: {e}")

        # Check if production model (symlink check)
        production_link = checkpoints_dir / "model_production.pt"
        if production_link.exists() and production_link.resolve() == pt_file:
            model_info["is_production"] = True
        else:
            model_info["is_production"] = False

        models.append(model_info)

    return {
        "success": True,
        "models": models,
        "count": len(models)
    }

@app.post("/training/models/{filename}/set-production")
async def set_production_model(filename: str):
    """Mark a model as the production model."""
    checkpoints_dir = Path(__file__).parent.parent / "ai" / "checkpoints_light"
    model_file = checkpoints_dir / filename

    if not model_file.exists():
        raise HTTPException(status_code=404, detail=f"Model {filename} not found")

    production_link = checkpoints_dir / "model_production.pt"

    try:
        # Remove existing symlink if it exists
        if production_link.exists() or production_link.is_symlink():
            production_link.unlink()

        # Create new symlink
        production_link.symlink_to(model_file.name)

        logger.info(f"Set {filename} as production model")

        return {
            "success": True,
            "filename": filename,
            "message": f"Model {filename} is now set as production"
        }

    except Exception as e:
        logger.error(f"Error setting production model: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/training/models/{filename}/metadata")
async def get_model_metadata(filename: str):
    """Get detailed metadata for a specific model."""
    checkpoints_dir = Path(__file__).parent.parent / "ai" / "checkpoints_light"
    model_file = checkpoints_dir / filename

    if not model_file.exists():
        raise HTTPException(status_code=404, detail=f"Model {filename} not found")

    metadata_file = checkpoints_dir / f"{Path(filename).stem}_metadata.json"

    if not metadata_file.exists():
        return {
            "success": True,
            "filename": filename,
            "metadata": {},
            "message": "No metadata file found"
        }

    try:
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        return {
            "success": True,
            "filename": filename,
            "metadata": metadata
        }

    except Exception as e:
        logger.error(f"Error loading metadata: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/training/aws-cost/{job_id}")
async def get_training_cost(job_id: str):
    """Calculate estimated cost for an AWS training job."""
    with training_lock:
        if job_id not in training_jobs:
            raise HTTPException(status_code=404, detail=f"Training job {job_id} not found")

        job = training_jobs[job_id]

    if not job.get("is_aws"):
        return {
            "success": True,
            "job_id": job_id,
            "cost_usd": 0.0,
            "message": "Not an AWS job"
        }

    # Instance pricing (hourly rates in USD)
    instance_prices = {
        "g4dn.xlarge": 0.526,      # T4 GPU, 16GB RAM
        "g4dn.2xlarge": 0.752,     # T4 GPU, 32GB RAM
        "p3.2xlarge": 3.06,        # V100 GPU, 61GB RAM
        "t3.large": 0.0832,        # CPU only, 8GB RAM
        "t3.xlarge": 0.1664        # CPU only, 16GB RAM
    }

    instance_type = job.get("instance_type", "g4dn.xlarge")
    hourly_rate = instance_prices.get(instance_type, 0.526)

    # Calculate duration
    start_time = job.get("start_time")
    end_time = job.get("end_time") or datetime.utcnow()

    # Convert to datetime if string
    if isinstance(start_time, str):
        start_time = datetime.fromisoformat(start_time)
    if isinstance(end_time, str):
        end_time = datetime.fromisoformat(end_time)

    duration_hours = (end_time - start_time).total_seconds() / 3600.0
    estimated_cost = duration_hours * hourly_rate

    return {
        "success": True,
        "job_id": job_id,
        "instance_type": instance_type,
        "hourly_rate_usd": hourly_rate,
        "duration_hours": round(duration_hours, 2),
        "cost_usd": round(estimated_cost, 2),
        "status": job["status"],
        "is_running": job["status"] in ["running", "launching"]
    }


# ============================================================================
# S3 DATA ENDPOINTS - Full Dataset Exploration
# ============================================================================

s3_data_source = None

def get_s3_source():
    """Get or create S3 data source singleton."""
    global s3_data_source
    if s3_data_source is None:
        s3_data_source = S3DataSource(
            bucket="qbia",
            prefix="bourse/mintrad",
            cache_dir="/tmp/trading_data_cache"
        )
    return s3_data_source

@app.get("/s3/years")
async def get_s3_years():
    """Obtenir toutes les années disponibles dans S3."""
    try:
        s3 = get_s3_source()
        years = s3.list_available_years()
        return {
            "success": True,
            "years": years,
            "count": len(years)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching years: {str(e)}")

@app.get("/s3/symbols/{year}")
async def get_s3_symbols(year: int):
    """Obtenir tous les symboles disponibles pour une année."""
    try:
        s3 = get_s3_source()
        symbols = s3.list_available_symbols(year)
        return {
            "success": True,
            "year": year,
            "symbols": symbols,
            "count": len(symbols)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching symbols: {str(e)}")

@app.get("/s3/data/{symbol}/{year}")
async def get_s3_symbol_data(symbol: str, year: int, limit: Optional[int] = 10000):
    """Obtenir les données d'un symbole pour une année."""
    try:
        s3 = get_s3_source()
        df = s3.fetch_symbol_data(symbol.upper(), year)

        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data found for {symbol} in {year}")

        # Limit data for frontend performance
        if limit and len(df) > limit:
            df = df.tail(limit)

        # Convert timestamp to ISO format
        df['timestamp'] = pd.to_datetime(df['timestamp']).dt.strftime("%Y-%m-%d %H:%M:%S")

        return {
            "success": True,
            "symbol": symbol.upper(),
            "year": year,
            "count": len(df),
            "data": df.to_dict(orient='records'),
            "stats": {
                "min_price": float(df['low'].min()),
                "max_price": float(df['high'].max()),
                "avg_price": float(df['close'].mean()),
                "total_volume": float(df['volume'].sum()),
                "start_date": df['timestamp'].iloc[0] if len(df) > 0 else None,
                "end_date": df['timestamp'].iloc[-1] if len(df) > 0 else None,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching data: {str(e)}")

@app.get("/s3/overview")
async def get_s3_overview():
    """Obtenir une vue d'ensemble de toutes les données S3 disponibles."""
    try:
        s3 = get_s3_source()
        years = s3.list_available_years()

        overview = {
            "success": True,
            "years": [],
            "total_symbols": 0,
            "symbols_by_year": {}
        }

        for year in years:
            symbols = s3.list_available_symbols(year)
            overview["symbols_by_year"][str(year)] = {
                "count": len(symbols),
                "symbols": symbols
            }
            overview["total_symbols"] += len(symbols)

        overview["years"] = years

        return overview
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching overview: {str(e)}")

@app.get("/s3/latest/{symbol}")
async def get_s3_latest_data(symbol: str, limit: int = 1000):
    """Obtenir les dernières données disponibles pour un symbole (année la plus récente)."""
    try:
        s3 = get_s3_source()
        years = s3.list_available_years()

        if not years:
            raise HTTPException(status_code=404, detail="No years available")

        # Try latest year first
        latest_year = max(years)
        df = s3.fetch_symbol_data(symbol.upper(), latest_year)

        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data found for {symbol}")

        # Limit data
        if len(df) > limit:
            df = df.tail(limit)

        df['timestamp'] = pd.to_datetime(df['timestamp']).dt.strftime("%Y-%m-%d %H:%M:%S")

        return {
            "success": True,
            "symbol": symbol.upper(),
            "year": latest_year,
            "count": len(df),
            "data": df.to_dict(orient='records')
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching latest data: {str(e)}")


# ============================================================================
# AI METRICS & MODEL PERFORMANCE ENDPOINTS
# ============================================================================

@app.get("/ai/model-metrics")
async def get_model_metrics():
    """Obtenir les métriques de performance du modèle IA."""
    # En production, ces métriques viendraient du modèle entraîné
    # Pour l'instant, on retourne des métriques simulées mais réalistes
    try:
        metrics = {
            "accuracy": 0.68 + random.random() * 0.1,
            "precision": 0.72 + random.random() * 0.08,
            "recall": 0.65 + random.random() * 0.1,
            "f1_score": 0.68 + random.random() * 0.08,
            "sharpe_ratio": 1.2 + random.random() * 0.4,
            "total_predictions": random.randint(500, 1000),
            "correct_predictions": random.randint(350, 700),
            "avg_confidence": 0.65 + random.random() * 0.15,
            "model_version": "MultiModalTransformer-v1.0",
            "last_updated": datetime.utcnow().isoformat()
        }

        return {
            "success": True,
            "metrics": metrics
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching model metrics: {str(e)}")

@app.get("/ai/feature-importance")
async def get_feature_importance():
    """Obtenir l'importance des features du modèle (SHAP values)."""
    # En production, ces valeurs viendraient de l'analyse SHAP du modèle
    features = [
        {"feature": "Price Momentum", "importance": 0.24, "description": "Rate of price change over time"},
        {"feature": "Volume Profile", "importance": 0.19, "description": "Trading volume patterns"},
        {"feature": "RSI (14)", "importance": 0.15, "description": "Relative Strength Index"},
        {"feature": "MACD Signal", "importance": 0.13, "description": "Moving Average Convergence Divergence"},
        {"feature": "Bollinger Bands", "importance": 0.11, "description": "Price volatility indicator"},
        {"feature": "Order Book Imbalance", "importance": 0.09, "description": "Bid-ask pressure"},
        {"feature": "Funding Rate", "importance": 0.08, "description": "Perpetual contract funding"},
        {"feature": "Fear & Greed Index", "importance": 0.06, "description": "Market sentiment"},
        {"feature": "Cross-Asset Correlation", "importance": 0.05, "description": "BTC/ETH correlation"},
        {"feature": "Temporal Attention", "importance": 0.04, "description": "Transformer attention weights"}
    ]

    return {
        "success": True,
        "features": features
    }

@app.get("/ai/decision-explanation/{symbol}")
async def get_decision_explanation(symbol: str):
    """Obtenir l'explication détaillée d'une décision de trading pour un symbole."""
    try:
        # En production, cela viendrait de l'analyse du modèle (attention weights, gradients, etc.)
        # Pour l'instant, on génère une explication simulée mais cohérente

        # Essayer d'obtenir la prédiction actuelle
        prediction = None
        if PIPELINE_AVAILABLE:
            try:
                prediction = pipeline_connector.get_prediction(symbol.upper())
            except:
                pass

        # Générer l'explication
        price_change = random.random() * 4 - 2  # -2% à +2%
        action = "BUY" if price_change > 1 else "SELL" if price_change < -1 else "HOLD"

        features = [
            {
                "name": "Price Momentum",
                "value": price_change > 0 and 0.78 or -0.65,
                "weight": 0.24,
                "impact": price_change > 0 and "positive" or "negative"
            },
            {
                "name": "Volume Profile",
                "value": random.random() * 0.8 - 0.4,
                "weight": 0.19,
                "impact": random.random() > 0.5 and "positive" or "negative"
            },
            {
                "name": "RSI (14)",
                "value": price_change < 0 and 0.55 or -0.42,
                "weight": 0.15,
                "impact": price_change < 0 and "positive" or "negative"
            },
            {
                "name": "MACD Signal",
                "value": random.random() * 0.6 - 0.3,
                "weight": 0.13,
                "impact": random.random() > 0.5 and "positive" or "negative"
            },
            {
                "name": "Order Book",
                "value": price_change > 0 and 0.35 or -0.28,
                "weight": 0.09,
                "impact": price_change > 0 and "positive" or "negative"
            }
        ]

        reasoning = []
        if price_change > 1:
            reasoning = [
                "Strong upward momentum detected across multiple timeframes",
                "Volume profile shows increasing buyer interest",
                "Technical indicators align for bullish continuation",
                "Transformer attention weights focus on recent price action"
            ]
        elif price_change < -1:
            reasoning = [
                "Downward pressure from weakening momentum",
                "RSI indicates overbought conditions",
                "Negative divergence in volume patterns",
                "Risk-off sentiment in correlated assets"
            ]
        else:
            reasoning = [
                "Market in consolidation phase",
                "Mixed signals across technical indicators",
                "Waiting for clearer directional bias"
            ]

        return {
            "success": True,
            "symbol": symbol.upper(),
            "action": action,
            "confidence": 0.5 + random.random() * 0.3,
            "timestamp": datetime.utcnow().isoformat(),
            "features": features,
            "reasoning": reasoning
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating explanation: {str(e)}")

@app.get("/ai/model-architecture")
async def get_model_architecture():
    """Obtenir les informations sur l'architecture du modèle."""
    return {
        "success": True,
        "architecture": {
            "model_type": "Multi-Modal Transformer",
            "encoder_layers": 2,
            "attention_heads": 4,
            "hidden_dimension": 128,
            "dropout": 0.1,
            "input_features": [
                "Price OHLCV",
                "Volume metrics",
                "Technical indicators",
                "Sentiment data",
                "Macro indicators"
            ],
            "output": "Price direction prediction with confidence",
            "training_samples": "~500K",
            "last_trained": "2024-12-14"
        }
    }

# ============================================================================
# DATA INTEGRITY ENDPOINTS
# ============================================================================

@app.get("/data-integrity/all")
async def get_all_data_integrity():
    """Analyse l'intégrité de toutes les cryptos."""
    try:
        analyzer = DataIntegrityAnalyzer()
        results = analyzer.analyze_all_cryptos()
        return results
    except Exception as e:
        logger.error(f"Error analyzing data integrity: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/data-integrity/{crypto}")
async def get_crypto_data_integrity(crypto: str):
    """Analyse l'intégrité d'une crypto spécifique."""
    try:
        analyzer = DataIntegrityAnalyzer()
        result = analyzer.analyze_crypto_data(crypto.upper())
        return result
    except Exception as e:
        logger.error(f"Error analyzing {crypto}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/data-integrity/available-cryptos")
async def get_available_cryptos():
    """Liste les cryptos disponibles dans le cache."""
    try:
        analyzer = DataIntegrityAnalyzer()
        cryptos = analyzer.get_available_cryptos()
        return {
            "cryptos": cryptos,
            "count": len(cryptos)
        }
    except Exception as e:
        logger.error(f"Error getting available cryptos: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/dataset/crypto-data/{crypto}")
async def get_crypto_historical_data(
    crypto: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 1000
):
    """Récupère les données historiques d'une crypto avec prix et métadonnées."""
    try:
        s3_cache_path = Path("ai/cache/s3_data")
        pattern = f"{crypto.upper()}USDT_*.parquet"
        files = list(s3_cache_path.glob(pattern))

        if not files:
            raise HTTPException(status_code=404, detail=f"No data found for {crypto}")

        # Charger les données
        dfs = []
        for file in sorted(files):
            df = pd.read_parquet(file)
            dfs.append(df)

        full_df = pd.concat(dfs, ignore_index=True)

        # Filtrer par date si nécessaire
        if 'timestamp' in full_df.columns:
            full_df['timestamp'] = pd.to_datetime(full_df['timestamp'])

            # Filtrer les dates futures
            now = pd.Timestamp.now(tz='UTC')
            full_df = full_df[full_df['timestamp'] <= now]

            full_df = full_df.sort_values('timestamp')

            if start_date:
                full_df = full_df[full_df['timestamp'] >= start_date]
            if end_date:
                full_df = full_df[full_df['timestamp'] <= end_date]

        # Limiter le nombre de lignes
        if len(full_df) > limit:
            # Prendre des échantillons uniformément répartis
            indices = np.linspace(0, len(full_df) - 1, limit, dtype=int)
            full_df = full_df.iloc[indices]

        # Convertir en JSON
        data = full_df.to_dict('records')

        # Convertir les timestamps en strings
        for record in data:
            if 'timestamp' in record:
                record['timestamp'] = str(record['timestamp'])

        return {
            "crypto": crypto.upper(),
            "total_rows": len(data),
            "data": data,
            "columns": full_df.columns.tolist()
        }

    except Exception as e:
        logger.error(f"Error fetching crypto data for {crypto}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("🚀 ALPHA TRADING API SERVER")
    print("=" * 80)
    print("\nStarting server on http://localhost:8000")
    print("API Documentation: http://localhost:8000/docs")
    print("\n📊 MARKET DATA ENDPOINTS:")
    print("  - GET /market/all-cryptos      - All cryptos with current & previous prices")
    print("  - GET /market/ticker           - Ticker data for a symbol")
    print("  - GET /market/klines           - Candlestick data (OHLCV)")
    print("  - GET /market/orderbook        - Order book depth")
    print("  - GET /market/trades           - Recent trades")
    print("\n📈 DATASET ENDPOINTS:")
    print("  - GET /dataset/summary         - Dataset summary")
    print("  - GET /dataset/signals         - Alpha signals")
    print("  - GET /dataset/ohlcv/{symbol}  - OHLCV data")
    print("  - GET /dataset/fear-greed      - Fear & Greed Index")
    print("  - GET /dataset/sentiment       - Reddit sentiment")
    print("  - GET /dataset/macro           - Macro data")
    print("  - GET /dataset/derivatives     - Derivatives data")
    print("\n🤖 REAL-TIME PIPELINE ENDPOINTS:")
    print("  - POST /pipeline/start         - Start real-time pipeline")
    print("  - POST /pipeline/stop          - Stop pipeline")
    print("  - GET /pipeline/status         - Pipeline status & stats")
    print("  - GET /pipeline/predictions    - All current predictions")
    print("  - GET /pipeline/prediction/{symbol} - Prediction for symbol")
    print("  - GET /pipeline/features/{symbol}   - Features for symbol")
    print("  - GET /pipeline/symbols        - Active symbols")
    print("\n" + "=" * 80 + "\n")

    uvicorn.run(app, host="0.0.0.0", port=8000)
