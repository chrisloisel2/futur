#!/usr/bin/env python3
"""
GPU Detection and Configuration Check for ML Training
======================================================

Checks:
1. TensorFlow GPU support
2. PyTorch CUDA support
3. NVIDIA driver/hardware
4. Provides recommendations
"""
import sys
import subprocess

print("="*80)
print("GPU DETECTION & CONFIGURATION")
print("="*80)

# ============================================================================
# 1. TensorFlow Check
# ============================================================================
print("\n📦 1. TensorFlow:")
try:
    import tensorflow as tf
    print(f"   Version: {tf.__version__}")

    gpus = tf.config.list_physical_devices('GPU')
    if gpus:
        print(f"   ✅ GPU detected: {len(gpus)} device(s)")
        for i, gpu in enumerate(gpus):
            print(f"      [{i}] {gpu.name}")

        # Check CUDA build
        built_with_cuda = tf.test.is_built_with_cuda()
        print(f"   Built with CUDA: {built_with_cuda}")

        if built_with_cuda:
            print(f"   ✅ TensorFlow can use GPU for training")
        else:
            print(f"   ⚠️  TensorFlow NOT built with CUDA")
            print(f"   → Reinstall: pip install tensorflow[and-cuda]")
    else:
        print(f"   ❌ No GPU detected by TensorFlow")
        print(f"   → TensorFlow will use CPU only")
        print(f"   → Install CUDA-enabled version: pip install tensorflow[and-cuda]")

except ImportError:
    print(f"   ⚠️  TensorFlow not installed")
    print(f"   → Install: pip install tensorflow[and-cuda]")
except Exception as e:
    print(f"   ❌ Error: {e}")

# ============================================================================
# 2. PyTorch Check
# ============================================================================
print("\n🔥 2. PyTorch:")
try:
    import torch
    print(f"   Version: {torch.__version__}")

    if torch.cuda.is_available():
        print(f"   ✅ CUDA available: True")
        print(f"   GPU Name: {torch.cuda.get_device_name(0)}")
        print(f"   CUDA Version: {torch.version.cuda}")

        props = torch.cuda.get_device_properties(0)
        memory_gb = props.total_memory / 1e9
        print(f"   Memory: {memory_gb:.1f} GB")
        print(f"   Compute Capability: {props.major}.{props.minor}")

        # Test tensor on GPU
        try:
            x = torch.randn(100, 100).cuda()
            print(f"   ✅ PyTorch can use GPU for training")
        except Exception as e:
            print(f"   ⚠️  GPU tensor creation failed: {e}")
    else:
        print(f"   ❌ CUDA not available")
        print(f"   → Install CUDA-enabled PyTorch:")
        print(f"   → pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121")

except ImportError:
    print(f"   ⚠️  PyTorch not installed")
    print(f"   → Install: pip install torch torchvision")
except Exception as e:
    print(f"   ❌ Error: {e}")

# ============================================================================
# 3. NVIDIA Hardware Check
# ============================================================================
print("\n🎮 3. NVIDIA Hardware:")
try:
    result = subprocess.run(
        ['nvidia-smi', '--query-gpu=name,memory.total,driver_version,cuda_version',
         '--format=csv,noheader'],
        capture_output=True,
        text=True,
        check=True,
        timeout=5
    )

    info = result.stdout.strip().split(', ')
    if len(info) >= 4:
        gpu_name, memory, driver, cuda = info
        print(f"   ✅ GPU: {gpu_name}")
        print(f"   Memory: {memory}")
        print(f"   Driver: {driver}")
        print(f"   CUDA: {cuda}")

        # Parse memory
        if 'MiB' in memory:
            mem_mb = int(memory.split()[0])
            mem_gb = mem_mb / 1024
            print(f"   Available VRAM: {mem_gb:.1f} GB")

            # Recommendations based on VRAM
            if mem_gb >= 8:
                print(f"   ✅ Sufficient VRAM for medium models (batch 64-128)")
            elif mem_gb >= 6:
                print(f"   ⚠️  Limited VRAM (batch 32-64 recommended)")
            else:
                print(f"   ⚠️  Low VRAM (batch 16-32 recommended)")
    else:
        print(f"   ✅ nvidia-smi output: {result.stdout.strip()}")

except FileNotFoundError:
    print(f"   ❌ nvidia-smi not found")
    print(f"   → Install NVIDIA drivers")
except subprocess.TimeoutExpired:
    print(f"   ⚠️  nvidia-smi timeout")
except Exception as e:
    print(f"   ❌ Error: {e}")

# ============================================================================
# 4. CUDA Toolkit Check
# ============================================================================
print("\n🔧 4. CUDA Toolkit:")
try:
    result = subprocess.run(
        ['nvcc', '--version'],
        capture_output=True,
        text=True,
        timeout=5
    )

    if result.returncode == 0:
        # Extract version
        for line in result.stdout.split('\n'):
            if 'release' in line.lower():
                print(f"   ✅ {line.strip()}")
                break
    else:
        print(f"   ⚠️  nvcc not found in PATH")

except FileNotFoundError:
    print(f"   ⚠️  CUDA Toolkit not installed")
    print(f"   → Not required if using tensorflow[and-cuda] or PyTorch wheels")
except Exception as e:
    print(f"   ❌ Error: {e}")

# ============================================================================
# 5. Recommendations
# ============================================================================
print("\n" + "="*80)
print("RECOMMENDATIONS")
print("="*80)

has_tf_gpu = False
has_torch_gpu = False

try:
    import tensorflow as tf
    has_tf_gpu = len(tf.config.list_physical_devices('GPU')) > 0
except:
    pass

try:
    import torch
    has_torch_gpu = torch.cuda.is_available()
except:
    pass

if has_tf_gpu:
    print("\n✅ TensorFlow GPU Ready")
    print("   → Modify train_event_classifier.py:")
    print("   → Add GPU memory limit + mixed precision")
    print("   → Increase batch_size from 32 to 128")
    print("   → Expected speedup: 5-8x")
elif has_torch_gpu:
    print("\n✅ PyTorch GPU Ready")
    print("   → Modify train_edge_forecaster.py:")
    print("   → Use model.to('cuda')")
    print("   → Enable mixed precision with autocast")
    print("   → Expected speedup: 5-10x")
else:
    print("\n⚠️  No GPU Framework Detected")
    print("\n📝 Installation Options:")
    print("\n   A) For TensorFlow (EventClassifier, etc.):")
    print("      pip uninstall tensorflow")
    print("      pip install tensorflow[and-cuda]")
    print("\n   B) For PyTorch (EdgeForecaster, etc.):")
    print("      pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121")
    print("\n   C) Verify after install:")
    print("      python check_gpu.py")

# ============================================================================
# 6. Training Configuration Example
# ============================================================================
if has_tf_gpu or has_torch_gpu:
    print("\n" + "="*80)
    print("TRAINING OPTIMIZATION EXAMPLE")
    print("="*80)

    if has_tf_gpu:
        print("\n📝 Add to train_event_classifier.py (after imports):")
        print("""
import tensorflow as tf

# GPU Configuration
gpus = tf.config.list_physical_devices('GPU')
if gpus:
    try:
        # Limit memory to 6GB (leave margin for system)
        tf.config.set_logical_device_configuration(
            gpus[0],
            [tf.config.LogicalDeviceConfiguration(memory_limit=6144)]
        )
        # Enable mixed precision (FP16) for RTX 30xx
        tf.keras.mixed_precision.set_global_policy('mixed_float16')
        print(f"✅ GPU: {gpus[0].name} | Memory: 6GB | FP16: enabled")
    except RuntimeError as e:
        print(f"GPU config failed: {e}")

# Increase batch size
@dataclass(frozen=True)
class CFG:
    batch_size: int = 128  # Was 32 (CPU)
    # ... rest of config
""")

    if has_torch_gpu:
        print("\n📝 Add to train_edge_forecaster.py:")
        print("""
import torch
from torch.cuda.amp import autocast, GradScaler

# GPU Configuration
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print(f"Using device: {device}")

# Enable TF32 for Ampere GPUs (RTX 30xx)
torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True

# Mixed precision scaler
scaler = GradScaler()

# Move model to GPU
model = model.to(device)

# Training loop with mixed precision
for batch in dataloader:
    x, y = batch
    x, y = x.to(device), y.to(device)

    with autocast():
        output = model(x)
        loss = criterion(output, y)

    scaler.scale(loss).backward()
    scaler.step(optimizer)
    scaler.update()
""")

print("\n" + "="*80)
print()
