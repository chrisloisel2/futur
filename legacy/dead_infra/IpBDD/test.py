import pymongo
import sys

# L'URI corrigée avec ?authSource=admin
MONGO_URI = "mongodb://admin:admin123@192.168.88.17/roger?authSource=admin"

print(f"🔌 Tentative de connexion à {MONGO_URI}")

try:
    client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    # Force une commande simple pour déclencher l'authentification
    client.admin.command('ping')
    print("✅ Connexion réussie ! Authentification OK.")
    # Liste les bases de données accessibles
    dbs = client.list_database_names()
    print(f"📀 Bases de données visibles : {dbs}")
except pymongo.errors.OperationFailure as e:
    print(f"❌ Échec d'authentification : {e}")
except pymongo.errors.ServerSelectionTimeoutError as e:
    print(f"❌ Impossible de joindre le serveur MongoDB : {e}")
except Exception as e:
    print(f"❌ Erreur inconnue : {e}")
