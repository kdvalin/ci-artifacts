#! /bin/bash

run_normal_tests_and_plots() {
    local BASE_ARTIFACT_DIR="$ARTIFACT_DIR"

    local test_flavor=$(get_config tests.notebooks.test_flavor)

    local test_failed=0
    local plot_failed=0
    local test_runs=$(get_config tests.notebooks.repeat)

    sutest_cleanup_rhods

    for idx in $(seq "$test_runs"); do
        if [[ "$test_runs" != 1 ]]; then
            export ARTIFACT_DIR="$BASE_ARTIFACT_DIR/$(printf "%03d" $idx)__test_run"
        fi

        mkdir -p "$ARTIFACT_DIR"
        local pr_file="$BASE_ARTIFACT_DIR"/pull_request.json
        local pr_comment_file="$BASE_ARTIFACT_DIR"/pull_request-comments.json
        for f in "$pr_file" "$pr_comment_file"; do
            [[ -f "$f" ]] && cp "$f" "$ARTIFACT_DIR" || true
        done

        run_test "$idx" && test_failed=0 || test_failed=1

        generate_plots || plot_failed=1

        if [[ "$test_failed" == 1 ]]; then
            break
        fi
    done

    if [[ "$plot_failed" != 0 ]]; then
        echo "One of the plotting step failed :/ "
        return "$plot_failed"
    fi

    if [[ "$test_failed" != 0 ]]; then
        echo "One of the testing step failed :/"
        return "$test_failed"
    fi

    return 0
}

run_locust_test() {
    switch_driver_cluster
    ./run_toolbox.py from_config rhods notebook_locust_scale_test

    local last_test_dir=$(printf "%s\n" "$ARTIFACT_DIR"/*__*/ | tail -1)
    set_config matbench.test_directory "$last_test_dir"
}

run_gating_tests_and_plots() {
    local BASE_ARTIFACT_DIR="$ARTIFACT_DIR"

    cp "$CI_ARTIFACTS_FROM_CONFIG_FILE" "$ARTIFACT_DIR/config.base.yaml"
    local test_idx=0
    local failed=0

    cat <<EOF > "$ARTIFACT_DIR/settings.gating"
test_flavor=notebooks_gating
version=$(get_config rhods.catalog.version)
EOF

    for preset in $(get_config tests.notebooks.gating_tests[])
    do
        test_idx=$((test_idx + 1)) # start at 1, 0 is prepare_steps

        # Wait a few minutes if this isn't the first test
        if [[ $test_idx -ne 1 ]]; then
            local WAIT_TIME=5m
            echo "Waiting $WAIT_TIME for the cluster to cool down before running the next test."
            sleep 5m
        fi

        # restore the initial configuration
        cp "$BASE_ARTIFACT_DIR/config.base.yaml" "$CI_ARTIFACTS_FROM_CONFIG_FILE"

        apply_preset "$preset"

        export ARTIFACT_DIR="$BASE_ARTIFACT_DIR/$(printf "%03d" $test_idx)__$preset/000__prepare_steps"

        prepare_failed=0
        if ! prepare; then
            prepare_failed=1
        else
            prepare_failed=0
        fi

        sutest_cleanup_rhods

        export ARTIFACT_DIR="$BASE_ARTIFACT_DIR/$(printf "%03d" $test_idx)__$preset"

        if [[ "$prepare_failed" == 1 ]]; then
            ARTIFACT_DIR="$BASE_ARTIFACT_DIR" _warning "Gating preset '$preset' preparation failed :/"
            failed=1
            continue
        fi

        if ! run_normal_tests_and_plots; then
            ARTIFACT_DIR="$BASE_ARTIFACT_DIR" _warning "Gating preset '$preset' test failed :/"
            failed=1
        fi
    done

    export ARTIFACT_DIR="$BASE_ARTIFACT_DIR"
    if [[ "$failed" == 1 ]]; then
        _warning "Gating test failed :/"
    fi

    python3 "$TESTING_UTILS_DIR/generate_plot_index.py" > "$ARTIFACT_DIR/report_index.html"

    return $failed
}

run_tests_and_plots() {
    local test_flavor=$(get_config tests.notebooks.test_flavor)

    if [[ "$test_flavor" == "gating" ]]; then
        run_gating_tests_and_plots
    else
        run_normal_tests_and_plots
    fi
}

run_test() {
    local repeat_idx=${1:-}

    local test_flavor=$(get_config tests.notebooks.test_flavor)
    if [[ "$repeat_idx" ]]; then
        mkdir -p "$ARTIFACT_DIR"
        echo "repeat=$repeat_idx" > "$ARTIFACT_DIR/settings.repeat"
    fi

    if [[ "$test_flavor" == "ods-ci" ]]; then
        run_ods_ci_test || return 1
    elif [[ "$test_flavor" == "locust" ]]; then
        run_locust_test "$repeat_idx" || return 1
    elif [[ "$test_flavor" == "notebook-performance" ]]; then
        run_single_notebook_tests "$repeat_idx" || return 1
    elif [[ "$test_flavor" == "gating" ]]; then
        # 'gating' testing is handled higher in the call stack, before the 'repeat' (in run_gating_tests_and_plots)
        _error "Test flavor cannot be '$test_flavor' in function run_test."
    else
        _error "Unknown test flavor: $test_flavor"
    fi

    switch_sutest_cluster
    if ! ./run_toolbox.py rhods capture_state > /dev/null; then
        _warning "rhods capture state failed :("
    fi
}
