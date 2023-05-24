# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/00_core.ipynb.

# %% auto 0
__all__ = ['logger', 'dirs', 'cache', 'retryer', 'load_config', 'login', 'azcli', 'datalake_path']

# %% ../nbs/00_core.ipynb 3
import logging, subprocess, os, sys, pandas
from platformdirs import PlatformDirs
from diskcache import Cache, memoize_stampede
from tenacity import wait_random_exponential, stop_after_attempt, Retrying
from upath import UPath
from benedict import benedict

# %% ../nbs/00_core.ipynb 5
logger = logging.getLogger(__name__)

# %% ../nbs/00_core.ipynb 7
dirs = PlatformDirs("nbdev-squ")
cache = Cache(dirs.user_cache_dir)
retryer = Retrying(wait=wait_random_exponential(), stop=stop_after_attempt(3), reraise=True)

# %% ../nbs/00_core.ipynb 8
def _cli(cmd: list[str], capture_output=True):
    cmd = [sys.executable, "-m", "azure.cli"] + cmd
    if capture_output: # Try lots, parse output as json
        try:
            result = retryer(subprocess.run, cmd + ["-o", "json"], capture_output=capture_output, check=True, text=True)
        except subprocess.CalledProcessError:
            # Clear login cache incase stale
            cache.delete("logged_in")
            # Run in foreground to see error
            subprocess.run(cmd, check=True)
        try:
            result = benedict(result.stdout, format="json")
        except ValueError: # handle if output not json
            result = result.stdout.strip() or benedict()
        return result
    else: # Run interactively, ignore success/fail
        subprocess.run(cmd)

# %% ../nbs/00_core.ipynb 10
def load_config(path = None # Path to read json config into cache from
               ):
    config = benedict()
    if path:
        config = benedict(path.read_text(), format="json")
    try:
        _cli(["config", "set", "extension.use_dynamic_install=yes_without_prompt"])
        config = benedict(_cli(["keyvault", "secret", "show", 
                                "--vault-name", cache["vault_name"], 
                                "--name", f"squconfig-{cache['tenant_id']}"]).value, format="json")
    except subprocess.CalledProcessError:
        cache.delete("logged_in") # clear the logged in state
    config.standardize()
    return config

def login(refresh: bool=False # Force relogin
         ):
    if "/" in os.environ.get("SQU_CONFIG", ""):
        cache["vault_name"], cache["tenant_id"] = os.environ["SQU_CONFIG"].split("/")
    tenant = cache.get("tenant_id")
    try:
        _cli(["account", "show"])
    except subprocess.CalledProcessError:
        cache.delete("logged_in")
    while not cache.get("logged_in"):
        logger.info("Cache doesn't look logged in, attempting login")
        try:
            # See if we can login with a managed identity in under 5 secs and see the configured tenant
            subprocess.run(["timeout", "5", sys.executable, "-m", "azure.cli", "login", "--identity", "-o", "none", "--allow-no-subscriptions"], check=True)
            if tenant:
                tenant_visible = len(_cli(["account", "list"]).search(tenant + 'as')) > 0
                assert tenant_visible > 0
        except subprocess.CalledProcessError:
            # If managed identity unavailable, fall back on a manual login
            if tenant:
                tenant_scope = ["--tenant", tenant]
            else:
                tenant_scope = []
            _cli(["login", *tenant_scope, "--use-device-code", "--allow-no-subscriptions", "-o", "none"], capture_output=False)
        # Finally, validate the login once more, and set the login state
        try:
            _cli(["account", "show"])
            cache.set("logged_in", True, 60 * 60 * 3) # cache login state for 3 hrs
        except subprocess.CalledProcessError:
            cache.delete("logged_in")
    logger.info("Cache state is logged in")
    if cache.get("vault_name"): # Always reload config on any login call
        logger.info("Loading config from keyvault")
        cache["config"] = load_config() # Config lasts forever, don't expire

# %% ../nbs/00_core.ipynb 13
def azcli(basecmd: list[str]):
    if not cache.get("logged_in"):
        login()
    return _cli(basecmd)

# %% ../nbs/00_core.ipynb 15
#@memoize_stampede(cache, expire=60 * 60 * 24)
def datalake_path(expiry_days: int=3, # Number of days until the SAS token expires
                  permissions: str="racwdlt" # Permissions to grant on the SAS token
                    ):
    if not cache.get("logged_in"): # Have to login to grab keyvault config
        login()
    expiry = pandas.Timestamp("now") + pandas.Timedelta(days=expiry_days)
    account = cache["config"]["datalake_account"].split(".")[0] # Grab the account name, not the full FQDN
    container = cache['config']['datalake_container']
    print(locals())
    sas = azcli(["storage", "container", "generate-sas", "--auth-mode", "login", "--as-user", 
                 "--account-name", account, "--name", container, "--permissions", permissions, "--expiry", str(expiry.date())])
    print(sas)
    return UPath(f"az://{container}", account_name=account, sas_token=sas)
