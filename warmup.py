import requests
from config import Config
config = Config()
print("Прогрев модели...")
try:
    requests.post(config.ollama + '/api/generate', json={
        'model': config.model,
        'prompt': 'Привет',
        'stream': False
    }, timeout=60).raise_for_status()
    print("Модель готова!")
except Exception as e:
    print(f"Ошибка прогрева: {e}")
