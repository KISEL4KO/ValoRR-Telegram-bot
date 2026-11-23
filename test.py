import re
import requests

URL = "https://valorant-api.com/v1/agents"
params = {"isPlayableCharacter": "true", "language": "ru-RU"}  # language можно убрать, тогда будет английский

resp = requests.get(URL, params=params, timeout=10)
resp.raise_for_status()
data = resp.json()["data"]

def clean(text: str | None) -> str:
    text = re.sub(r"</?[a-z]*>", "", text or "")   # <teal>, </>, <whitebold> ...
    return re.sub(r"\s+", " ", text).strip()        # убираем \r\n и лишние пробелы

for agent in data:
    print(agent["displayName"])
    for a in agent.get("abilities") or []:
        print(f"  [{a['slot']}] {a['displayName']}: {clean(a['description'])}")