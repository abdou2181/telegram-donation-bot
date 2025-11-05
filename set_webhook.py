import requests

TOKEN = "YOUR_BOT_TOKEN_HERE"
WEBHOOK_URL = "https://your-project.vercel.app/webhook/YOUR_BOT_TOKEN_HERE"

url = f"https://api.telegram.org/bot{TOKEN}/setWebhook"
data = {"url": WEBHOOK_URL}
response = requests.post(url, data=data)
print(response.json())
