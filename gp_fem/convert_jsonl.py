from pathlib import Path, PurePath
import json

src = Path("data/dialogs_style_converted.sharegpt.jsonl")
dst = Path("data/dialogs_style_converted.norm.jsonl")

ok = 0
skip = 0

with src.open("r", encoding="utf-8") as fin, dst.open("w", encoding="utf-8") as fout:
    for ln, line in enumerate(fin, 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception as e:
            print(f"!! pulando linha {ln}: JSON inválido -> {e}")
            skip += 1
            continue

        # Esperado: {"messages":[{"from":"human"/"assistant","value":"..."}], ...}
        msgs = obj.get("messages")
        if not isinstance(msgs, list) or not all(isinstance(m, dict) for m in msgs):
            print(f"!! pulando linha {ln}: 'messages' ausente/fora do formato")
            skip += 1
            continue

        fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        ok += 1

print(f"✅ normalizado: {ok} linhas | ❌ puladas: {skip}")
print(f"➡️ saída: {dst}")
