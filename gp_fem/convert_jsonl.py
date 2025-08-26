import json
from pathlib import Path

def normalize_extras_paid(profile: dict):
    """Normaliza extras_paid para SEMPRE ser lista de objetos."""
    ep = profile.get("extras_paid", [])
    if ep is None:
        return []
    if isinstance(ep, dict):
        return [ep]
    if isinstance(ep, list):
        new_list = []
        for item in ep:
            if item is None:
                continue
            if isinstance(item, dict):
                new_list.append(item)
            elif isinstance(item, str):
                new_list.append({"type": item})
            else:
                new_list.append({"type": str(item)})
        return new_list
    if isinstance(ep, str):
        return [{"type": ep}]
    return [{"type": str(ep)}]

def convert_jsonl(input_file, output_file):
    total, fixed = 0, 0
    with open(input_file, 'r', encoding='utf-8') as infile, open(output_file, 'w', encoding='utf-8') as outfile:
        for line in infile:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                print(f"❌ Erro ao parsear linha em {input_file}: {line[:100]}...")
                continue

            total += 1

            # Normalizar perfil se existir
            if isinstance(data.get("profile"), dict):
                data["profile"]["extras_paid"] = normalize_extras_paid(data["profile"])
                fixed += 1

            # Normalizar mensagens
            if "messages" in data:
                new_messages = []
                for msg in data["messages"]:
                    role = msg.get("role")
                    content = msg.get("content", "")
                    new_msg = {
                        "from": "human" if role == "user" else "assistant",
                        "value": content
                    }
                    new_messages.append(new_msg)
                data["messages"] = new_messages

            json.dump(data, outfile, ensure_ascii=False)
            outfile.write("\n")

    print(f"✅ {input_file} → {output_file} | total: {total} | normalizadas: {fixed}")

# Caminhos
base = Path("/workspaces/whatsapp-autoresponder/data")

convert_jsonl(base / "dialogs_style.sharegpt.jsonl",
              base / "dialogs_style_converted.sharegpt.jsonl")

convert_jsonl(base / "dialogs_style_with_personality.sharegpt.jsonl",
              base / "dialogs_style_with_personality_converted.sharegpt.jsonl")