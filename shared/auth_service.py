import os
from dataclasses import dataclass

from .logging_redirect import redirect_std_streams


SCOPES = ["basic", "message_all"]


@dataclass(frozen=True)
class AuthResult:
    success: bool
    message: str
    title: str
    token_path: str | None = None


def run_o365_auth(token_dir: str, consent_handler, logger=print) -> AuthResult:
    with redirect_std_streams(logger):
        logger("\n🔐 Iniciando flujo de autenticación O365...")

        client_id = os.getenv("GRAPH_CLIENT_ID")
        tenant_id = os.getenv("GRAPH_TENANT_ID")

        if not client_id or not tenant_id:
            logger("❌ Error: Faltan variables en el archivo .env.")
            return AuthResult(
                success=False,
                title="Faltan Credenciales",
                message="No se encontraron GRAPH_CLIENT_ID o GRAPH_TENANT_ID en el archivo .env.",
            )

        from O365 import Account, FileSystemTokenBackend

        try:
            credentials = (client_id, "")
            token_backend = FileSystemTokenBackend(
                token_path=token_dir,
                token_filename="o365_token.txt",
            )
            account = Account(
                credentials,
                auth_flow="authorization",
                tenant_id=tenant_id,
                token_backend=token_backend,
            )

            if account.authenticate(scopes=SCOPES, handle_consent=consent_handler):
                token_path = os.path.join(token_dir, "o365_token.txt")
                logger(f"\n✅ ¡Autenticación exitosa! Token guardado en: {token_path}")
                return AuthResult(
                    success=True,
                    title="Éxito",
                    message="¡Token generado y autenticación exitosa!",
                    token_path=token_path,
                )

            logger("\n❌ La autenticación falló.")
            return AuthResult(
                success=False,
                title="Fallo",
                message="La autenticación falló. Revisa las credenciales e intenta de nuevo.",
            )
        except Exception as exc:
            logger(f"❌ Error en autenticación: {exc}")
            return AuthResult(
                success=False,
                title="Error",
                message=f"Ocurrió un error: {exc}",
            )
