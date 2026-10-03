"""bcrypt wrappers. bcrypt is deliberately slow (~250ms); call these via
asyncio.to_thread from request handlers so one login doesn't freeze the
single event loop for every other user."""
import bcrypt


def bcrypt_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def bcrypt_check(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())
