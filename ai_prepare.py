"""Initialize only new AI secrets; never alters or overwrites existing leads."""
from leadfinder.ai_runtime import bridge_token,crypt,Registry
from leadfinder.config import app_paths

if __name__=='__main__':
    data=app_paths().data
    bridge_token(data)
    crypt(data)
    Registry(data)
    print('AI gateway secret and Gemini encryption key are ready.')
    print('Keep *.secret and ai_encryption.key private and backed up.')
