Decomposed `build_parser()` into per-module `register_subparser()` functions so each subcommand's argument definitions live alongside their handlers in the corresponding `commands_*.py` module.
