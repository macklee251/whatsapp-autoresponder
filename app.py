def send_text(to_number_e164: str, text: str) -> bool:
    """Envia mensagem de texto via UltraMsg e loga a resposta JSON."""
    inst = (INSTANCE_ID or "").strip()
    token = (ULTRA_TOKEN or "").strip()

    if not (inst and token):
        print("[ULTRA] credenciais ausentes (ULTRA_INSTANCE_ID/ULTRAMSG_TOKEN).")
        return False

    # UltraMsg prefere token COMO GET PARAM na URL
    url = f"{API_URL}/{inst}/messages/chat?token={token}"
    payload = {"to": to_number_e164, "body": text}

    try:
        r = requests.post(url, data=payload, timeout=15)
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text}

        print("[ULTRA] URL:", f"{API_URL}/{inst}/messages/chat?token=***")
        print("[ULTRA] resp:", r.status_code, body)

        # respostas típicas de sucesso do UltraMsg:
        # {"sent":true, "id":"..."}  ou  {"status":"ok","message":"Message has been sent"}
        if r.ok and (body.get("sent") is True or body.get("status") in ("ok",) or body.get("message") == "Message has been sent"):
            if body.get("id"):
                print("[ULTRA] message_id:", body["id"])
            return True
        return False
    except Exception as e:
        print("[ULTRA] exceção:", e)
        return False