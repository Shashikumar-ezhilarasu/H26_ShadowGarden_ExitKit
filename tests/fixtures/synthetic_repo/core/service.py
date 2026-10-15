from requests import Session


class ApiService:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.session = Session()

    def send_data(self, endpoint: str, payload: dict) -> None:
        url = f"{self.base_url}/{endpoint}"
        self.session.post(url, json=payload)

    def close(self) -> None:
        self.session.close()
