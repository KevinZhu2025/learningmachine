AUTH_ORIGIN = "http://127.0.0.1:5173"


def authenticate(client, user_id: str, password: str = "correct-horse-battery"):
    headers = {"Origin": AUTH_ORIGIN}
    registration = client.post(
        "/api/auth/register",
        headers=headers,
        json={"userId": user_id, "password": password},
    )
    assert registration.status_code == 201, registration.text
    login = client.post(
        "/api/auth/login",
        headers=headers,
        json={"userId": user_id, "password": password},
    )
    assert login.status_code == 200, login.text
    client.headers.update(
        {
            "Origin": AUTH_ORIGIN,
            "X-CSRF-Token": login.json()["csrfToken"],
        }
    )
    return login.json()
