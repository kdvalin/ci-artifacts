#! /bin/bash

set -o pipefail
set -o errexit
set -o nounset
set -o errtrace
set -x

TESTING_NOTEBOOKS_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
TESTING_UTILS_DIR="$TESTING_NOTEBOOKS_DIR/../utils"

source "$TESTING_NOTEBOOKS_DIR/configure.sh"
source "$TESTING_UTILS_DIR/logging.sh"

clusters_create__check_test_size() {
    if [[ "${OPENSHIFT_CI:-}" == true ]]; then
        if [[ "$JOB_NAME_SAFE" != *-long && "$JOB_NAME_SAFE" != long ]]; then
            # not running in a long test

            if [[ "$(get_config tests.notebooks.test_flavor)" == gating ]]; then
                _error "refusing to run the notebook gating scale test outside of a '-long' test. (JOB_NAME_SAFE=$JOB_NAME_SAFE)"
                return 1
            fi

            local user_count=$(get_config tests.notebooks.users.count)
            if [[ "$user_count" -gt 300 ]]; then
                _error "refusing to run the notebook scale test with $user_count users outside of a '-long' test. (JOB_NAME_SAFE=$JOB_NAME_SAFE)"
                return 1
            fi
        fi
    fi
}

export -f clusters_create__check_test_size

exec "$TESTING_UTILS_DIR/openshift_clusters/clusters.sh" "$@"
