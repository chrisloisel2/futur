#!/usr/bin/env python3
"""
Vérifie que toutes les corrections sont en place
"""

import sys
import os

def check_file(filepath, search_string, description, should_contain=True):
    """Check if a file contains (or doesn't contain) a specific string"""
    try:
        with open(filepath, 'r') as f:
            content = f.read()

        found = search_string in content

        if should_contain:
            if found:
                print(f"  ✅ {description}")
                return True
            else:
                print(f"  ❌ {description} - NOT FOUND")
                return False
        else:
            if not found:
                print(f"  ✅ {description}")
                return True
            else:
                print(f"  ❌ {description} - STILL PRESENT")
                return False
    except FileNotFoundError:
        print(f"  ❌ {description} - FILE NOT FOUND: {filepath}")
        return False

def main():
    print("\n" + "="*80)
    print("VERIFICATION DES CORRECTIONS")
    print("="*80 + "\n")

    all_ok = True

    # 1. Check model.py
    print("1. CHECKING ai/models/model.py")
    print("-" * 80)

    checks = [
        ("ai/models/model.py", "y_rv_agg = np.zeros((N,), dtype=np.float32)",
         "RV agregation (scalar array)"),
        ("ai/models/model.py", "y_dir[idx] = 1 if cum >= 0.0 else 0",
         "Direction binaire (cum >= 0)"),
        ("ai/models/model.py", "Dense(2),  # CHANGED: Binary",
         "Direction head: 2 classes"),
        ("ai/models/model.py", "Dense(1),  # CHANGED: Scalar output",
         "RV head: scalar output"),
        ("ai/models/model.py", "tf.squeeze(y_rv, axis=-1)",
         "RV squeeze to scalar"),
        ("ai/models/model.py", "tf.keras.losses.Huber(delta=0.01)",
         "RV loss: Huber"),
        ("ai/models/model.py", "tf.keras.losses.SparseCategoricalCrossentropy()",
         "Direction loss: SparseCategorical"),
    ]

    for filepath, search, desc in checks:
        if not check_file(filepath, search, desc):
            all_ok = False

    print()

    # 2. Check data_pipeline.py
    print("2. CHECKING ai/data_pipeline.py")
    print("-" * 80)

    checks = [
        ("ai/data_pipeline.py", "y_rv: np.ndarray  # [N] - SCALAR",
         "WindowsData.y_rv comment: scalar"),
        ("ai/data_pipeline.py", "Xw, y_ret_h, y_dir, y_rv_agg = make_windows",
         "create_windows_for_year: uses y_rv_agg"),
    ]

    for filepath, search, desc in checks:
        if not check_file(filepath, search, desc):
            all_ok = False

    print()

    # 3. Check data_pipeline_memory_efficient.py (CRITICAL!)
    print("3. CHECKING ai/data_pipeline_memory_efficient.py (CRITICAL)")
    print("-" * 80)

    checks = [
        ("ai/data_pipeline_memory_efficient.py",
         "'rv': tf.TensorSpec(shape=(), dtype=tf.float32),  # CORRECTED: Scalar RV",
         "TensorSpec RV: scalar shape ()"),
    ]

    # Also check that the WRONG version is NOT present
    wrong_checks = [
        ("ai/data_pipeline_memory_efficient.py",
         "'rv': tf.TensorSpec(shape=(horizon,), dtype=tf.float32)",
         "TensorSpec RV: NOT (horizon,)",
         False),  # should_contain=False
    ]

    for filepath, search, desc in checks:
        if not check_file(filepath, search, desc, should_contain=True):
            all_ok = False

    for filepath, search, desc, should_contain in wrong_checks:
        if not check_file(filepath, search, desc, should_contain=should_contain):
            all_ok = False

    print()

    # 4. Check config
    print("4. CHECKING ai/configs/train_corrected.yaml")
    print("-" * 80)

    checks = [
        ("ai/configs/train_corrected.yaml", "w_dir: 0.8",
         "Loss weight w_dir: 0.8"),
        ("ai/configs/train_corrected.yaml", "w_rv: 0.3",
         "Loss weight w_rv: 0.3"),
        ("ai/configs/train_corrected.yaml", "lr: 0.0003",
         "Learning rate: 0.0003"),
        ("ai/configs/train_corrected.yaml", "d_model: 128",
         "Model capacity: d_model=128"),
    ]

    for filepath, search, desc in checks:
        if not check_file(filepath, search, desc):
            all_ok = False

    print()

    # Summary
    print("="*80)
    if all_ok:
        print("✅ ALL CORRECTIONS VERIFIED!")
        print("="*80)
        print()
        print("Next steps:")
        print("  1. Sur le serveur: ./cleanup_server_windows.sh")
        print("  2. Lancer training: python3 ai/train_advanced.py --config ai/configs/train_corrected.yaml")
        print("  3. Vérifier que Dataset montre: 'rv': TensorSpec(shape=(), ...)")
        print("  4. Training doit démarrer sans ValueError")
        print()
        return 0
    else:
        print("❌ SOME CORRECTIONS MISSING!")
        print("="*80)
        print()
        print("Please review the failed checks above.")
        print()
        return 1

if __name__ == "__main__":
    sys.exit(main())
