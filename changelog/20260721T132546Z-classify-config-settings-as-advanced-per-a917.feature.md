Mark rarely-changed config settings as "advanced" in the JSON Schema so the
central-deploy Configure UI can hide them behind its "Show advanced settings"
toggle.  Only 15 expert/tuning fields are flagged; must-set hostnames,
Secrets, and ``log_level`` remain always-visible.
