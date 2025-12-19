import numpy as np
import matplotlib.pyplot as plt
import sys

def analyze_npz(file_path):
    data = np.load(file_path)

    print("Fichier :", file_path)
    print("Contenu :", data.files)
    print("-" * 40)

    for key in data.files:
        array = data[key]
        print(f"Nom        : {key}")
        print(f"Type       : {type(array)}")
        print(f"Shape      : {array.shape}")
        print(f"Dtype      : {array.dtype}")

        if array.ndim > 0:
            print("Extrait    :", array.flat[:5])
        else:
            print("Valeur     :", array)

        print("-" * 40)

        if array.ndim == 2:
            plt.figure()
            plt.title(key)
            plt.imshow(array)
            plt.colorbar()
            plt.show()

    data.close()

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage : python analyze_npz.py fichier.npz")
        sys.exit(1)

    analyze_npz(sys.argv[1])
