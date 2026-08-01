Web UI add-account now seeds the per-account settings store and initializes the
new account's database immediately, ensuring the account config is persisted in
the managed configuration plane. The reconcile loop reloads accounts from the
config file on every cycle so newly added accounts begin fetching mail without
a restart. On boot, accounts discovered from existing settings stores are merged
with config-file accounts so web-UI-added accounts survive even when the deploy
system overwrites ``config/config.json``.
