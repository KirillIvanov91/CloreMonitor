import requests

def test_whattomine_api():
    url = "https://whattomine.com/coins.json"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()  # Вызовет ошибку, если статус != 200
        data = response.json()
        print("✅ Данные получены:", len(data["coins"]), "монет")
        print(data)
    except requests.exceptions.RequestException as e:
        print("❌ Ошибка:", e)

if __name__ == "__main__":
    test_whattomine_api()