import os
import sys
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
import plaid
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.products import Products
from plaid.model.country_code import CountryCode
from plaid.exceptions import ApiException

from utils import clean_env

load_dotenv()

PLAID_ENV_MAP = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}


class PlaidClient:
    def __init__(self):
        client_id = clean_env(os.getenv("PLAID_CLIENT_ID"), "PLAID_CLIENT_ID")
        secret = clean_env(os.getenv("PLAID_SECRET"), "PLAID_SECRET")
        env_key = clean_env(os.getenv("PLAID_ENV", "sandbox"), "PLAID_ENV").lower()

        if env_key == "development":
            print("⚠️  PLAID_ENV=development is deprecated by Plaid; using Production instead")
            env_key = "production"

        host = PLAID_ENV_MAP.get(env_key, plaid.Environment.Sandbox)
        configuration = plaid.Configuration(host=host, api_key={"clientId": client_id, "secret": secret})
        api_client = plaid.ApiClient(configuration)
        self.client = plaid_api.PlaidApi(api_client)
        self.env_str = env_key

    def get_link_token(self) -> str:
        request = LinkTokenCreateRequest(
            user=LinkTokenCreateRequestUser(client_user_id="spending-tracker-user"),
            client_name="Spending Tracker",
            products=[Products("transactions")],
            country_codes=[CountryCode("US")],
            language="en",
        )
        response = self.client.link_token_create(request)
        return response["link_token"]

    def exchange_public_token(self, public_token: str) -> str:
        request = ItemPublicTokenExchangeRequest(public_token=public_token)
        response = self.client.item_public_token_exchange(request)
        return response["access_token"]

    def verify_access_token(self, access_token: str) -> bool:
        try:
            self.client.accounts_get(AccountsGetRequest(access_token=access_token))
            return True
        except ApiException as e:
            import json
            body = json.loads(e.body) if isinstance(e.body, str) else e.body
            error_code = body.get("error_code", "")
            if error_code in ("INVALID_ACCESS_TOKEN", "ITEM_LOGIN_REQUIRED"):
                return False
            raise

    def get_transactions(self, access_token: str, start_date: date, end_date: date) -> list:
        max_retries = 4
        retry_delay = 30
        for attempt in range(1, max_retries + 1):
            try:
                request = TransactionsGetRequest(
                    access_token=access_token,
                    start_date=start_date,
                    end_date=end_date,
                    options=TransactionsGetRequestOptions(count=500, offset=0),
                )
                response = self.client.transactions_get(request)
                transactions = response.transactions
                total = response.total_transactions
                while len(transactions) < total:
                    request = TransactionsGetRequest(
                        access_token=access_token,
                        start_date=start_date,
                        end_date=end_date,
                        options=TransactionsGetRequestOptions(count=500, offset=len(transactions)),
                    )
                    response = self.client.transactions_get(request)
                    transactions += response.transactions
                return [self._serialize_transaction(t) for t in transactions]
            except ApiException as e:
                body = e.body if hasattr(e, "body") else str(e)
                if "PRODUCT_NOT_READY" in str(body):
                    print(f"⏳ Plaid still preparing transactions (attempt {attempt}/{max_retries}), waiting {retry_delay}s...")
                    if attempt < max_retries:
                        time.sleep(retry_delay)
                        continue
                raise
        raise RuntimeError("Plaid transactions not ready after max retries")

    def get_accounts(self, access_token: str) -> list:
        response = self.client.accounts_get(AccountsGetRequest(access_token=access_token))
        return [
            {
                "account_id": a["account_id"],
                "name": a["name"],
                "type": str(a["type"]),
                "subtype": str(a["subtype"]),
                "mask": a.get("mask", ""),
            }
            for a in response["accounts"]
        ]

    @staticmethod
    def _serialize_transaction(t) -> dict:
        pfc = t.get("personal_finance_category") or {}
        return {
            "transaction_id": t["transaction_id"],
            "account_id": t["account_id"],
            "name": t["name"],
            # Plaid sign convention: positive = money leaving the account (expense/debit),
            # negative = money entering the account (income/credit). Applies to depository
            # and credit accounts alike — a credit card purchase is positive here.
            "amount": float(t["amount"]),
            "date": str(t["date"]),
            "pending": t.get("pending", False),
            "personal_finance_category": {
                "primary": pfc.get("primary", ""),
                "detailed": pfc.get("detailed", ""),
            },
        }
