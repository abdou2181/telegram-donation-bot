import requests

TOKEN = "7906357564:AAFMuF8SlDXThQ0Vhux1JUGY2x-Tepi7_Gs"
WEBHOOK_URL = "https://telegram-donation-bot.vercel.app/webhook/7906357564:AAFMuF8SlDXThQ0Vhux1JUGY2x-Tepi7_Gs"

url = f"https://api.telegram.org/bot{TOKEN}/setWebhook"
data = {"url": WEBHOOK_URL}
response = requests.post(url, data=data)
print(response.json())
