#!/bin/sh
# Development builds and graphics probes run on GitHub infrastructure only.
case "${GITHUB_ACTIONS:-}:${CODESPACES:-}" in
    true:*|*:true) ;;
    *)
        printf '%s\n' 'Gluon development builds and probes must run in GitHub Actions or Codespaces.' >&2
        printf '%s\n' 'Local execution is disabled after the owner stopped graphics testing on this Mac.' >&2
        exit 1
        ;;
esac
