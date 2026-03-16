Environment configuration
=========================

This project reads environment variables (PEXELS_API_KEY, etc.) from the environment and from an optional `.env` file.

Steps to configure:

1) Copy the example file:

   cp .env.example .env

2) Edit `.env` and set `PEXELS_API_KEY` to your Pexels API key.

3) The CLI and GUI automatically load `.env` using `python-dotenv` when present. If you prefer to load it in your shell for the current session:

   set -a; source .env; set +a

4) The repository already ignores `.env` in `.gitignore`.

If you need to change Kokoro model paths or Ollama host, update the corresponding variables in `.env`.
