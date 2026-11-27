import requests
import time
print("Прогрев модели...")
try:
    requests.post('http://127.0.0.1:11434/api/generate', json={
        'model': 'qwen3:8b',
        'prompt': 'Привет',
        'stream': False
    }, timeout=60)
    print("Модель готова!")
except Exception as e:
    print(f"Ошибка прогрева: {e}")