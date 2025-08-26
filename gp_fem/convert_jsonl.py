import json

def convert_jsonl(input_file, output_file):
    with open(input_file, 'r', encoding='utf-8') as infile, open(output_file, 'w', encoding='utf-8') as outfile:
        for line in infile:
            try:
                data = json.loads(line.strip())
                if 'messages' in data:
                    new_messages = []
                    for msg in data['messages']:
                        new_msg = {
                            'from': 'human' if msg['role'] == 'user' else 'assistant',
                            'value': msg['content']
                        }
                        new_messages.append(new_msg)
                    data['messages'] = new_messages
                json.dump(data, outfile, ensure_ascii=False)
                outfile.write('\n')
            except json.JSONDecodeError:
                print(f"Erro ao processar linha em {input_file}: {line.strip()}")

# Converter os dois arquivos
convert_jsonl('/workspaces/whatsapp-autoresponder/data/dialogs_style.sharegpt.jsonl',
              '/workspaces/whatsapp-autoresponder/data/dialogs_style_converted.sharegpt.jsonl')
convert_jsonl('/workspaces/whatsapp-autoresponder/data/dialogs_style_with_personality.sharegpt.jsonl',
              '/workspaces/whatsapp-autoresponder/data/dialogs_style_with_personality_converted.sharegpt.jsonl')