Fixed ten documentation links that pointed outside the docs directory with
relative paths (`../config/config.example.json`, `../entrypoint.sh`, and
similar). They were dead for anyone reading the published site, and they failed
the strict docs build — which nobody saw, because the Docs workflow had never
run. They now point at the files on GitHub.
