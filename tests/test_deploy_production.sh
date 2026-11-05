#!/usr/bin/env bash
# Behavioural checks for the deployment guard.
#
# The guard is almost entirely refusals, so that is what this tests: the cases
# it must decline, each one standing for something that actually happened.
#
# git is real, not stubbed. The rollback rule is an ancestry question, and
# `git merge-base --is-ancestor` is the thing under test -- a stub would test
# the stub. Each case builds a throwaway repository with a real origin.
#
# docker, curl and systemctl are stubbed, driven by files in $STUB_DIR, so a
# test can produce a state a real daemon will not produce on demand: an orphan
# container surviving --remove-orphans, a config hash that does not match, a
# certificate mounted from the wrong place.
set -uo pipefail

SCRIPT="${SCRIPT_UNDER_TEST:-$(cd "$(dirname "$0")/.." && pwd)/scripts/deploy_production.sh}"

PASS=0; FAIL=0

# The tally reports on the exit path, not from the last line of the file.
#
# It used to be two statements at the bottom: an echo and `[ "$FAIL" -eq 0 ]`.
# The exit status of a script is the status of its last command, so a section
# appended below them ran, counted its failures into $FAIL, and left the status
# of whatever it happened to end with -- an `rm -rf`, which succeeds. One real
# FAIL was reported in the output and the job went green (found while adding
# the post-deploy behaviour cases).
#
# On the trap it holds wherever the last case is written.
_report_tally() {
    local rc=$?
    echo
    echo "  passed: $PASS   failed: $FAIL"
    if [ "$FAIL" -ne 0 ]; then
        exit 1
    fi
    exit "$rc"
}
trap _report_tally EXIT
ok()    { PASS=$((PASS+1)); printf '  ok    %s\n' "$1"; }
bad()   { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 — expected [$3], got [$2]"; fi; }

# Refuses for the stated reason, not merely refuses. Every precondition here
# exits non-zero, so a status alone cannot tell "wrong branch" from "the stub
# fell over", and a test that cannot tell them apart passes for the wrong
# reason.
refused_because() {
    local label="$1" want="$2"
    if [ "$RC" = 0 ]; then bad "$label — expected a refusal, got exit 0"; return; fi
    if grep -qi -- "$want" "$SANDBOX/out"; then ok "$label"; else
        bad "$label — refused, but not for '$want': $(grep -i REFUSED "$SANDBOX/out" | head -1)"
    fi
}

new_sandbox() {
    SANDBOX="$(mktemp -d)"
    STUB_DIR="$SANDBOX/stub"; mkdir -p "$STUB_DIR/bin"
    ORIGIN="$SANDBOX/origin.git"
    REPO="$SANDBOX/repo"

    # git -C throughout rather than cd, so no test can leave the shell somewhere
    # unexpected and no cd needs an error path of its own.
    git init -q --bare "$ORIGIN"
    git clone -q "$ORIGIN" "$REPO" 2>/dev/null
    git -C "$REPO" config user.email t@t
    git -C "$REPO" config user.name t
    mkdir -p "$REPO/nginx"
    {
        echo "upstream sora_backend {"
        echo "    server backend:8000;"
        echo "}"
    } > "$REPO/nginx/nginx.conf"
    # Bind-mounted the same way nginx.conf is, and the reason #275 exists: the
    # container pins the inode it started with, so the file changing on disk is
    # not the file prometheus is reading.
    mkdir -p "$REPO/infra"
    {
        echo "global:"
        echo "  scrape_interval: 15s"
        echo "scrape_configs:"
        echo '  - job_name: "sora-app"'
        echo "    static_configs:"
        echo '      - targets: ["backend:8000"]'
        # Two jobs, not one. With a single job "every declared job is up" and
        # "the one job is up" are the same sentence, and a check that dropped
        # all but the first would pass.
        echo '  - job_name: "sora-scheduler"'
        echo "    static_configs:"
        echo '      - targets: ["scheduler:9000"]'
    } > "$REPO/infra/prometheus.yml"
    echo "services: {}" > "$REPO/compose.yml"
    # Present so the deployment runs its account-bootstrap step; the deploy
    # script runs it only where the checked-out code has it.
    mkdir -p "$REPO/scripts"
    echo "# stand-in" > "$REPO/scripts/bootstrap_users.py"
    git -C "$REPO" add -A >/dev/null
    git -C "$REPO" commit -qm "first"
    git -C "$REPO" branch -M main >/dev/null 2>&1
    git -C "$REPO" push -q origin main 2>/dev/null
    FIRST="$(git -C "$REPO" rev-parse HEAD)"
    echo "second" > "$REPO/second.txt"
    git -C "$REPO" add -A >/dev/null
    git -C "$REPO" commit -qm "second"
    git -C "$REPO" push -q origin main 2>/dev/null
    SECOND="$(git -C "$REPO" rev-parse HEAD)"

    CONF_SUM="$(sha256sum "$REPO/nginx/nginx.conf" | awk '{print $1}')"
    : > "$STUB_DIR/services"          # declared services, one per line
    echo "nginx" >> "$STUB_DIR/services"
    echo "backend" >> "$STUB_DIR/services"
    : > "$STUB_DIR/running"           # containers: name|service|ports
    echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" >> "$STUB_DIR/running"
    echo "p-backend-1|backend|8000/tcp" >> "$STUB_DIR/running"
    echo "$CONF_SUM" > "$STUB_DIR/container_conf_sum"
    # The default is a container that did read the repository's file, i.e. a
    # correct deployment. A case that needs a stale container overwrites this.
    cp "$REPO/infra/prometheus.yml" "$STUB_DIR/prometheus_live_conf"
    # What `promtool query instant ... up` prints: one line per target, the
    # trailing number being the last scrape's result. The default is both
    # targets healthy; a case that needs a dead one rewrites the file.
    {
        echo 'up{instance="backend:8000", job="sora-app"} => 1 @[1788785159.269]'
        echo 'up{instance="scheduler:9000", job="sora-scheduler"} => 1 @[1788785159.269]'
    } > "$STUB_DIR/prometheus_up_lines"
    # `resolve` is required since #129: without it nginx caches the address
    # and a container recreate strands it.
    echo "    server backend:8000 resolve;" > "$STUB_DIR/upstream_line"
    echo "/etc/letsencrypt" > "$STUB_DIR/cert_source"
    echo 200 > "$STUB_DIR/http_code"

    cat > "$STUB_DIR/bin/docker" <<'STUB'
#!/usr/bin/env bash
argv="$*"
# Every invocation is recorded. "Refused before anything started" is otherwise
# unfalsifiable: the guard exits non-zero either way, and only the absence of an
# `up` tells prevention from detection.
printf '%s\n' "$argv" >> "$STUB_DIR/calls"

# The image a compose command would run `$1` from. A real compose resolves the
# tag when the command runs; with the deployment's pin override on the command
# line (`-f .../sora-image-pin.*`) it takes the id written there instead. That
# difference is the whole point of the pin, so the stub models both halves:
# `tag_moved` is the tag pointing somewhere else after the build.
pin_image_for() {
    local f="" prev="" a
    for a in $argv; do
        if [ "$prev" = "-f" ]; then case "$a" in *sora-image-pin*) f="$a" ;; esac; fi
        prev="$a"
    done
    [ -n "$f" ] && [ -f "$f" ] || return 0
    awk -v s="  $1:" '$0 == s {on = 1; next} /^  [^ ]/ {on = 0}
        on && /image:/ {gsub(/"/, "", $2); print $2; exit}' "$f"
}
tag_image() {
    if [ -f "$STUB_DIR/tag_moved" ]; then cat "$STUB_DIR/tag_moved"
    else cat "$STUB_DIR/app_image_id" 2>/dev/null || echo "sha256:image"; fi
}
image_for() {
    local p; p="$(pin_image_for "$1")"
    if [ -n "$p" ]; then echo "$p"; else tag_image; fi
}
# An `up` that is not the targeted recreate still decides whether backend
# matches its configuration. Without the pin the configuration names the tag,
# so once the tag has moved compose recreates backend from it.
unpinned_up_moves_backend() {
    if [ -f "$STUB_DIR/app_recreated" ] && [ -z "$(pin_image_for backend)" ] && [ -f "$STUB_DIR/tag_moved" ]; then
        cat "$STUB_DIR/tag_moved" > "$STUB_DIR/recreated_image"
        echo "unpinned up recreated backend" >> "$STUB_DIR/migration_log"
    fi
}
# The state docker reports for one service's containers. `state_<service>` is a
# test's fixed answer; otherwise `live_<service>`, which stop and recreate
# maintain; otherwise running.
service_state() {
    if [ -f "$STUB_DIR/state_$1" ]; then cat "$STUB_DIR/state_$1"
    elif [ -f "$STUB_DIR/live_$1" ]; then echo "cid-$1 $(cat "$STUB_DIR/live_$1")"
    else echo "cid-$1 running"; fi
}
container_alive() {
    kill -0 "$1" 2>/dev/null || return 1
    case "$(ps -o stat= -p "$1" 2>/dev/null)" in Z*) return 1 ;; esac
    return 0
}
# A probe answer, then the exit status a test asks for: the stdout of a psql
# that then failed is the case `|| true` used to lose.
answer() { printf '%s\n' "$2"; exit "$(cat "$STUB_DIR/${1}_exit" 2>/dev/null || echo 0)"; }

case "$argv" in
    # Before "config --services", which would otherwise not match this at all --
    # kept adjacent so the two cannot drift apart.
    *"config --format json"*)  cat "$STUB_DIR/rendered"; exit 0 ;;
    # Every service, profiles included: what the image pin may name. The
    # one-shot services live behind profiles in docker-compose.prod.yml.
    *"--profile * config --services"*)
        if [ -f "$STUB_DIR/all_services" ]; then cat "$STUB_DIR/all_services"
        else cat "$STUB_DIR/services"; printf '%s\n' scheduler migrate bootstrap-users; fi
        exit 0 ;;
    *"config --services"*)  cat "$STUB_DIR/services"; exit 0 ;;
    *"ps --format {{.Service}}"*)
        cut -d'|' -f2 "$STUB_DIR/running"; exit 0 ;;
    *'{{.Label "com.docker.compose.service"}}|{{.Ports}}'*)
        # Before the label-only branch, which would otherwise swallow this.
        # Field 2 is the compose service, field 3 the ports.
        awk -F'|' '{print $2"|"$3}' "$STUB_DIR/running"; exit 0 ;;
    *'{{.Label "com.docker.compose.service"}} {{.Image}} {{.ID}}'*)
        awk -F'|' '{print $2" image-"$2" id-"$2}' "$STUB_DIR/running"; exit 0 ;;
    *'{{.Label "com.docker.compose.service"}}'*)
        cut -d'|' -f2 "$STUB_DIR/running"; exit 0 ;;
    *'{{.Names}}|{{.Ports}}'*)
        awk -F'|' '{print $1"|"$3}' "$STUB_DIR/running"; exit 0 ;;
    *'{{.Names}} {{.Ports}}'*)
        awk -F'|' '{print "  "$1" "$3}' "$STUB_DIR/running"; exit 0 ;;
    # The migrator: `compose run -d`, a creating client that returns once the
    # container exists and has been started, and a container that then runs on
    # its own. The container is the detached subshell below, its pid in
    # `migrator_pid`, its existence in `migrator_container`, its exit code in
    # `migrator_rc`; it is addressed by the id `cidmigrator0001`.
    #
    # The schema changes at the commit and not before: `slow_migrate` holds the
    # transaction open for that many seconds, and a migrator stopped inside it
    # leaves the schema as it was -- which is the whole question the rollback
    # barrier has to get right. `migration_adds_ownership` is a release whose
    # migration is the ownership revision; without it the stub migrates
    # something unrelated, as every release before it did.
    #
    # The creating client can be slow: `migrate_create_delay` seconds before
    # the daemon registers the container. A signal can land in that time, and
    # the container still appears afterwards -- the case a fixed "settle"
    # window after killing the client could miss whenever the registration came
    # later than the window. The script now waits for the client instead, and
    # refuses outright when it does not return.
    *"run -d --no-deps"*" migrate")
        n=$(( $(cat "$STUB_DIR/migrate_calls" 2>/dev/null || echo 0) + 1 ))
        echo "$n" > "$STUB_DIR/migrate_calls"
        printf 'migrate %s\n' "$(image_for migrate)" >> "$STUB_DIR/ran_images"
        # `migrate_create_lost`: the response is lost -- the client exits 1
        # having seen nothing -- and the create the daemon had accepted lands
        # that many seconds later, then commits. Nothing the script can see at
        # the client's exit tells this from a create that was refused.
        if [ -f "$STUB_DIR/migrate_create_lost" ]; then
            ( sleep "$(cat "$STUB_DIR/migrate_create_lost")"
              : > "$STUB_DIR/migrator_container"
              echo "registered" >> "$STUB_DIR/migration_log"
              sleep 1
              if [ -f "$STUB_DIR/migrator_container" ]; then
                  printf '%s\n' evaluations.owner_user_id evaluations.data_scope \
                      predictions_log.owner_user_id predictions_log.data_scope \
                      table:project_access_events table:project_access_targets \
                      >> "$STUB_DIR/schema"
                  echo "committed" >> "$STUB_DIR/migration_log"
              fi ) < /dev/null > /dev/null 2>&1 &
            echo "stub: error during connect: unexpected EOF" >&2
            exit 1
        fi
        [ -f "$STUB_DIR/migrate_create_delay" ] && sleep "$(cat "$STUB_DIR/migrate_create_delay")"
        : > "$STUB_DIR/migrator_container"
        echo "registered" >> "$STUB_DIR/migration_log"
        # `migrate_start_fails`: the container exists and its start failed --
        # a known object, which the rollback can remove and confirm gone.
        [ -f "$STUB_DIR/migrate_start_fails" ] && { echo "stub: start failed" >&2; exit 1; }
        (
            [ -f "$STUB_DIR/slow_migrate" ] && sleep "$(cat "$STUB_DIR/slow_migrate")"
            if [ -f "$STUB_DIR/migrate_fails" ]; then echo 1 > "$STUB_DIR/migrator_rc"; exit 1; fi
            if [ -f "$STUB_DIR/migration_adds_ownership" ]; then
                printf '%s\n' evaluations.owner_user_id evaluations.data_scope \
                    predictions_log.owner_user_id predictions_log.data_scope \
                    table:project_access_events table:project_access_targets \
                    >> "$STUB_DIR/schema"
                echo "committed" >> "$STUB_DIR/migration_log"
            fi
            # A signal that arrives after the commit, while the migrator is still
            # finishing: the columns are visible and must block the old code.
            [ -f "$STUB_DIR/slow_after_commit" ] && sleep "$(cat "$STUB_DIR/slow_after_commit")"
            echo 0 > "$STUB_DIR/migrator_rc"
            exit 0
        ) < /dev/null > /dev/null 2>&1 &
        echo "$!" > "$STUB_DIR/migrator_pid"
        echo "p-migrate-run"
        exit 0 ;;
    # The migrator's container, by name (`ps -aq --filter name=`) or by id.
    # Before the generic `ps -aq` branch, which would answer for the project.
    *"ps -aq --no-trunc --filter name="*)
        [ -f "$STUB_DIR/migrator_ps_unreadable" ] && { echo "stub: cannot connect" >&2; exit 1; }
        [ -f "$STUB_DIR/migrator_name_ps_fails" ] && { echo "stub: cannot connect" >&2; exit 1; }
        [ -f "$STUB_DIR/migrator_container" ] && echo "cidmigrator0001"
        exit 0 ;;
    "inspect -f {{.Id}} "*)
        [ -f "$STUB_DIR/migrator_ps_unreadable" ] && { echo "stub: cannot connect" >&2; exit 1; }
        # By id only: the run reads the id by name once, and every later
        # question uses the id -- this fails the later ones.
        case "$argv" in *cidmigrator0001*)
            [ -f "$STUB_DIR/migrator_inspect_by_id_fails" ] && { echo "stub: cannot connect to the daemon" >&2; exit 1; } ;;
        esac
        if [ -f "$STUB_DIR/migrator_container" ]; then echo "cidmigrator0001"; exit 0; fi
        # Docker 29 (production since 2026-11) spells it in lower case.
        if [ -f "$STUB_DIR/docker29_messages" ]; then echo "error: no such object: ${argv##* }" >&2; exit 1; fi
        echo "Error: No such object: ${argv##* }" >&2; exit 1 ;;
    "inspect -f {{.State.Status}} "*)
        [ -f "$STUB_DIR/migrator_container" ] || { echo "Error: No such object" >&2; exit 1; }
        if [ -f "$STUB_DIR/migrator_pid" ] && container_alive "$(cat "$STUB_DIR/migrator_pid")"; then echo running
        else echo exited; fi
        exit 0 ;;
    "inspect -f {{.State.ExitCode}} "*)
        [ -f "$STUB_DIR/migrator_container" ] || { echo "Error: No such object" >&2; exit 1; }
        cat "$STUB_DIR/migrator_rc" 2>/dev/null || echo 137
        exit 0 ;;
    "logs cidmigrator"*)
        echo "stub migrator: alembic upgrade head"; exit 0 ;;
    "stop -t 10 cidmigrator"*)
        echo "migrator-stopped" >> "$STUB_DIR/migration_log"
        [ -f "$STUB_DIR/migrator_unstoppable" ] && exit 1
        if [ -f "$STUB_DIR/migrator_pid" ]; then
            pid="$(cat "$STUB_DIR/migrator_pid")"
            kill -TERM "$pid" 2>/dev/null
            # `docker stop` returns once the container has stopped.
            i=0
            while container_alive "$pid" && [ "$i" -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
            [ -f "$STUB_DIR/migrator_rc" ] || echo 137 > "$STUB_DIR/migrator_rc"
        fi
        exit 0 ;;
    "rm -f cidmigrator"*)
        [ -f "$STUB_DIR/migrator_unstoppable" ] && exit 1
        [ -f "$STUB_DIR/migrator_pid" ] && kill -KILL "$(cat "$STUB_DIR/migrator_pid")" 2>/dev/null
        rm -f "$STUB_DIR/migrator_container"
        echo "migrator-removed" >> "$STUB_DIR/migration_log"
        exit 0 ;;
    # Before the generic "ps -q": the rollback asks for ids, and a test needs to
    # be able to say a container existed before this run started.
    *"ps -aq"*)
        # A superset once the mutation has run: the ids that existed plus the
        # one this run created. Returning the same list both times left the
        # `created` set empty, so `docker stop` never ran and the ownership
        # assertion held for the wrong reason -- an unconditional
        # `docker stop $now` would have passed it too.
        [ -f "$STUB_DIR/pre_cids" ] && cat "$STUB_DIR/pre_cids"
        [ -f "$STUB_DIR/calls" ] && grep -qE 'up -d (--build|--no-build) --remove-orphans' "$STUB_DIR/calls" \
            && echo "cid-created-by-this-run"
        exit 0 ;;
    # Before every other `up`/`run` branch: the migration step must be
    # distinguishable from a container start, or "migrated once, first" cannot
    # be told from "started something".
    # The tag's id, and a way for a test to make the running container disagree
    # with it. Without a branch here the id comes back empty and the identity
    # check refuses every deployment for the wrong reason.
    *"inspect -f {{.Created}}"*)
        # Container age. `container_created` lets a test place a container
        # before the interrupted run began, which is the case an image
        # comparison alone cannot reject.
        cat "$STUB_DIR/container_created" 2>/dev/null || echo "2026-08-14T03:39:28.100020191Z"
        exit 0 ;;
    # Before the behaviour-probe branch, which matches on
    # `exec -T backend python3 -` and would otherwise swallow this: the probe's
    # pattern is a prefix of `python3 -c`. That collision answered the guard
    # question with a route table, and the guard check silently compared a JSON
    # blob against the string "production".
    *"exec -T backend python3 -c"*)
        cat "$STUB_DIR/live_sora_env" 2>/dev/null || echo "production"
        exit 0 ;;
    *"exec -T backend python3 -"*)
        # The post-deploy behaviour probe. Reads its own route table from the
        # running app, so the stub answers with a route table and the codes a
        # correct deployment produces; a case that wants a defect overwrites
        # `probe_json`.
        if [ -f "$STUB_DIR/probe_json" ]; then
            cat "$STUB_DIR/probe_json"
        else
            printf '%s\n' '{"absent_path":"/api/v1/__deploy_probe_absent__","absent":404,"get_only_path":"/api/v1/ab/stats","get_only":405}'
        fi
        exit 0 ;;
    *"verify_schema_head.py"*)
        [ -f "$STUB_DIR/schema_behind" ] && {
            echo "REFUSING TO START: the database is behind" >&2; exit 1; }
        echo "schema check: schema is at head"
        exit 0 ;;
    *"image inspect"*)
        # `tag_moves_after_build`: something retags the image right after the
        # deployment read its id -- another build, a pull, an operator. The
        # first answer is the id that was built; the tag moves as soon as it
        # has been given, and from then on names whatever the file says.
        tag_image
        if [ -f "$STUB_DIR/tag_moves_after_build" ] && [ ! -f "$STUB_DIR/tag_moved" ]; then
            cat "$STUB_DIR/tag_moves_after_build" > "$STUB_DIR/tag_moved"
        fi
        exit 0 ;;
    *"build backend"*)
        [ -f "$STUB_DIR/build_fails" ] && exit 1
        exit 0 ;;
    # The state of every container of a service, by label -- the replicas
    # included (service_state above).
    *"--format {{.ID}} {{.State}}"*)
        [ -f "$STUB_DIR/app_state_unreadable" ] && exit 1
        svc="${argv##*com.docker.compose.service=}"; svc="${svc%% *}"
        service_state "$svc"
        exit 0 ;;
    *" stop nginx")
        [ -f "$STUB_DIR/nginx_stop_fails" ] && exit 1
        touch "$STUB_DIR/nginx_stopped"; exit 0 ;;
    *" stop -t "*" backend scheduler")
        [ -f "$STUB_DIR/app_stop_fails" ] && exit 1
        # A second stop, after a start that failed partway, can fail on its own.
        [ -f "$STUB_DIR/app_recreated" ] && [ -f "$STUB_DIR/restop_fails" ] && exit 1
        echo exited > "$STUB_DIR/live_backend"
        echo exited > "$STUB_DIR/live_scheduler"
        touch "$STUB_DIR/apps_stopped"
        if [ -f "$STUB_DIR/app_recreated" ]; then
            echo "apps-stopped-after-start" >> "$STUB_DIR/migration_log"
        else
            echo "apps-stopped" >> "$STUB_DIR/migration_log"
        fi
        exit 0 ;;
    # Questions to PostgreSQL. Each answers in the exact form the script
    # requires, then exits with `<probe>_exit` when a test sets one -- valid
    # stdout from a psql that failed afterwards.
    *"exec -T postgres sh -c"*)
        case "$argv" in
            # The ownership barrier. Counted from `schema`, one line per column
            # or table the catalogue holds -- and a line counts only if the
            # question actually names it, so a probe that stopped asking about
            # one of the four columns is caught by that column's case.
            *"'ownership:'"*)
                echo "ownership-probe" >> "$STUB_DIR/migration_log"
                [ -f "$STUB_DIR/ownership_unreadable" ] && exit 1
                if [ -f "$STUB_DIR/ownership_answer" ]; then
                    cat "$STUB_DIR/ownership_answer"
                    exit "$(cat "$STUB_DIR/ownership_exit" 2>/dev/null || echo 0)"
                fi
                n=0
                while IFS= read -r item; do
                    case "$item" in
                        "") ;;
                        table:*) case "$argv" in *"'${item#table:}'"*) n=$((n + 1)) ;; esac ;;
                        *) t="${item%%.*}"; c="${item#*.}"
                           case "$argv" in *"'$t'"*) case "$argv" in *"'$c'"*) n=$((n + 1)) ;; esac ;; esac ;;
                    esac
                done < <(cat "$STUB_DIR/schema" 2>/dev/null)
                answer ownership "ownership:$n" ;;
            *"'active:'"*)
                [ -f "$STUB_DIR/active_unreadable" ] && exit 1
                # `active_text`: an exact reply, malformed on purpose.
                [ -f "$STUB_DIR/active_text" ] && { cat "$STUB_DIR/active_text"; exit 0; }
                # `active_after_migrate`: sessions that appear only once the
                # migrator has started -- its own, left behind when it is
                # stopped, which the pre-migration drain cannot see.
                if [ -f "$STUB_DIR/active_after_migrate" ] && [ -f "$STUB_DIR/migrate_calls" ]; then
                    answer active "active:$(cat "$STUB_DIR/active_after_migrate")"
                fi
                answer active "active:$(cat "$STUB_DIR/active_sessions" 2>/dev/null || echo 0)" ;;
            *"'migrator:'"*)
                [ -f "$STUB_DIR/migrator_session_unreadable" ] && exit 1
                # `migrator_sessions`: a server-side session that outlives its
                # client. Each termination round reports what it terminated;
                # the session is gone once the count reaches zero.
                left="$(cat "$STUB_DIR/migrator_sessions" 2>/dev/null || echo 0)"
                if [ "$left" -gt 0 ]; then
                    echo $((left - 1)) > "$STUB_DIR/migrator_sessions"
                    echo "migrator-session-terminated" >> "$STUB_DIR/migration_log"
                    answer migrator "migrator:1"
                fi
                echo "migrator-session-gone" >> "$STUB_DIR/migration_log"
                answer migrator "migrator:0" ;;
            # The persistent-accounts barrier. `users_table` is t or f; a
            # failing query prints nothing, which must refuse.
            *"'users:'"*)
                [ -f "$STUB_DIR/users_table_unreadable" ] && exit 1
                case "$(cat "$STUB_DIR/users_table" 2>/dev/null || echo f)" in
                    t) answer users "users:true" ;;
                    *) answer users "users:false" ;;
                esac ;;
        esac
        echo "stub: unrecognised question to postgres" >&2
        exit 1 ;;
    *"run --rm --no-deps bootstrap-users"*)
        n=$(( $(cat "$STUB_DIR/bootstrap_calls" 2>/dev/null || echo 0) + 1 ))
        echo "$n" > "$STUB_DIR/bootstrap_calls"
        printf 'bootstrap-users %s\n' "$(image_for bootstrap-users)" >> "$STUB_DIR/ran_images"
        [ -f "$STUB_DIR/bootstrap_fails" ] && exit 1
        exit 0 ;;
    *"up -d --wait postgres"*)
        [ -f "$STUB_DIR/postgres_fails" ] && exit 1
        exit 0 ;;
    *"up -d postgres"*)
        echo "postgres start must wait for health before migrations" >&2
        exit 1 ;;
    # The targeted recreate of the two application services. Before the
    # `up -d --no-build` branches, which would take it for the rollback's
    # exact-image restore and mark a rollback that never happened.
    #
    # `app_recreate_partial`: compose fails the pair after starting backend
    # and before scheduler -- the state a refusal must not describe as stopped.
    *"--no-deps --force-recreate backend scheduler"*)
        touch "$STUB_DIR/app_recreated"
        image_for backend > "$STUB_DIR/recreated_image"
        printf 'backend %s\nscheduler %s\n' "$(image_for backend)" "$(image_for scheduler)" >> "$STUB_DIR/ran_images"
        if [ -f "$STUB_DIR/app_recreate_partial" ]; then
            echo running > "$STUB_DIR/live_backend"
            echo created > "$STUB_DIR/live_scheduler"
            echo "partial-start" >> "$STUB_DIR/migration_log"
            exit 1
        fi
        echo running > "$STUB_DIR/live_backend"
        echo running > "$STUB_DIR/live_scheduler"
        [ -f "$STUB_DIR/app_recreate_fails" ] && exit 1
        exit 0 ;;
    *"up -d --no-build --no-deps "*)
        printf '%s\n' "$argv" >> "$STUB_DIR/deps_started"
        exit 0 ;;
    *"up -d --no-build --remove-orphans"*)
        # Both the deployment start and the rollback restore spell this the same
        # way, each with a second `-f`: the deployment's is the image pin, the
        # restore's the recorded images. Told apart by the override's name. The
        # earlier rule -- "two -f means the restore" -- became wrong the moment
        # the deployment pinned its image, and matching on the text alone once
        # swallowed the restore so that four rollback cases tested nothing.
        case "$argv" in
            *sora-image-restore*)
                touch "$STUB_DIR/rolled_back"
                # Says why on stderr, as compose does, so a test can tell a log that
                # kept the command's output from one that recorded only its status.
                [ -f "$STUB_DIR/no_build_fails" ] && { echo "stub-compose: exact-image restore refused" >&2; exit 1; }
                exit 0 ;;
        esac
        # The deployment start.
        unpinned_up_moves_backend
        [ -f "$STUB_DIR/slow_up" ] && sleep 6
        n=$(( $(cat "$STUB_DIR/up_calls" 2>/dev/null || echo 0) + 1 ))
        echo "$n" > "$STUB_DIR/up_calls"
        if [ -f "$STUB_DIR/up_fails_after_first" ] && [ "$n" -gt 1 ]; then exit 1; fi
        exit 0 ;;

    *"up -d --no-build"*)
        # The exact-image restore. Recorded so a test can tell it from a
        # rebuild, which is a different operation with a different verdict.
        touch "$STUB_DIR/rolled_back"
        [ -f "$STUB_DIR/no_build_fails" ] && exit 1
        exit 0 ;;
    *"up -d --build"*)
        # `slow_up` holds the mutation open so a test can interrupt it. Without
        # it the window between `up` and the postflight is too short to hit.
        [ -f "$STUB_DIR/slow_up" ] && sleep 6
        # A rollback rebuilds, so this is called twice. `up_fails_after_first`
        # lets a test fail the second one and nothing else -- which is what
        # separates "refused, previous state restored" from "refused, and the
        # rollback did not complete".
        n=$(( $(cat "$STUB_DIR/up_calls" 2>/dev/null || echo 0) + 1 ))
        echo "$n" > "$STUB_DIR/up_calls"
        if [ -f "$STUB_DIR/up_fails_after_first" ] && [ "$n" -gt 1 ]; then exit 1; fi
        exit 0 ;;
    # Its own branch rather than falling through to `*)`, which succeeds
    # unconditionally. The change under test is that this failing must not roll
    # a good deployment back, and with no way to make it fail that assertion
    # would pass against a bare, unguarded command too.
    # nginx depends on backend, so its `up` also reconciles backend.
    *"up -d --force-recreate nginx")
        unpinned_up_moves_backend
        exit 0 ;;
    *"--force-recreate --no-deps prometheus"*)
        touch "$STUB_DIR/prometheus_recreated"
        [ -f "$STUB_DIR/prometheus_recreate_fails" ] && exit 1
        exit 0 ;;
    *"promtool query instant"*)
        # `promtool_fails` is prometheus refusing to answer at all, which is a
        # third state: not "scraping" and not "declared but down".
        [ -f "$STUB_DIR/promtool_fails" ] && exit 1
        cat "$STUB_DIR/prometheus_up_lines"; exit 0 ;;
    *"exec -T prometheus cat /etc/prometheus/prometheus.yml"*)
        # `prometheus_conf_unreadable` is the daemon answering non-zero with no
        # output -- a container that is up but whose config cannot be read,
        # which is not the same as one holding the wrong config.
        [ -f "$STUB_DIR/prometheus_conf_unreadable" ] && exit 1
        cat "$STUB_DIR/prometheus_live_conf"; exit 0 ;;
    *"ps -q nginx"*)        echo "cid-nginx"; exit 0 ;;
    *"ps -q"*)              echo "cid-x"; exit 0 ;;
    *"sha256sum /etc/nginx/nginx.conf"*)
        echo "$(cat "$STUB_DIR/container_conf_sum")  /etc/nginx/nginx.conf"; exit 0 ;;
    *"nginx -t"*)
        [ -f "$STUB_DIR/nginx_t_fails" ] && exit 1
        exit 0 ;;
    # Both spellings: the check used `grep -A2 \'upstream sora_backend\'` and
    # now uses an awk range written `upstream[[:space:]]+sora_backend`, which
    # the literal glob no longer matched -- the stub fell through, returned
    # nothing, and every deployment test refused with an empty upstream.
    *"upstream sora_backend"*|*"sora_backend[[:space:]]"*|*"+sora_backend"*)
        # `upstream_cmd_fails` reproduces what a real daemon did: the command
        # exits non-zero with no output -- `grep` finding nothing -- which under
        # `set -e` ends the script outside any `fail`.
        [ -f "$STUB_DIR/upstream_cmd_fails" ] && exit 1
        cat "$STUB_DIR/upstream_line"; exit 0 ;;
    *"inspect"*"Destination"*)  cat "$STUB_DIR/cert_source"; exit 0 ;;
    # Before the generic image branch: the snapshot reads the compose service
    # off each container, and without this it comes back empty and the rollback
    # silently degrades to a rebuild.
    *"inspect -f {{index .Config.Labels"*)
        echo "${STUB_SERVICE:-nginx}"; exit 0 ;;
    *"inspect -f {{.Image}}"*)
        # `restored_image` lets a test say the image after the rollback is not
        # the one recorded -- the case the verification exists for.
        # `stale_app_image` is production on 2026-10-26: `up --no-build` left
        # the containers on the previous image although the tag had moved. Once
        # the targeted recreate has run they report the built image, unless
        # `recreate_does_not_fix` says the recreate changed nothing either.
        if [ -f "$STUB_DIR/rolled_back" ] && [ -f "$STUB_DIR/restored_image" ]; then
            cat "$STUB_DIR/restored_image"
        elif [ -f "$STUB_DIR/app_recreated" ] && [ ! -f "$STUB_DIR/recreate_does_not_fix" ]; then
            # What the recreate (or a later unpinned `up`) actually started from.
            cat "$STUB_DIR/recreated_image" 2>/dev/null || tag_image
        elif [ -f "$STUB_DIR/stale_app_image" ]; then
            cat "$STUB_DIR/stale_app_image"
        else
            echo "sha256:image"
        fi
        exit 0 ;;
    *) exit 0 ;;
esac
STUB
    # One status per line, consumed in order; the last line repeats once the list
    # runs out. That is what tells a transient failure from a permanent one --
    # with a single fixed status the retry loop cannot be observed at all, and a
    # broken retry would pass.
    # The rendered compose configuration, as `config --format json` returns it.
    # Production's actual shape: nginx off-host on 80 and 443, everything else
    # on loopback. A test that needs a different one overwrites this file.
    cat > "$STUB_DIR/rendered" <<'JSON'
{"services": {
  "nginx":    {"ports": [{"target": 80,  "published": "80",  "protocol": "tcp", "mode": "ingress"},
                         {"target": 443, "published": "443", "protocol": "tcp", "mode": "ingress"}]},
  "app":      {"ports": [{"host_ip": "127.0.0.1", "target": 8000, "published": "8000", "protocol": "tcp", "mode": "ingress"}]},
  "postgres": {"ports": [{"host_ip": "127.0.0.1", "target": 5432, "published": "5432", "protocol": "tcp", "mode": "ingress"}]},
  "backend":   {"environment": {"SORA_ENV": "production"}},
  "scheduler": {"environment": {"SORA_ENV": "production"}}
}}
JSON

    cat > "$STUB_DIR/bin/curl" <<'STUB'
#!/usr/bin/env bash
seq_file="$STUB_DIR/http_code"
n_file="$STUB_DIR/http_calls"
n=$(( $(cat "$n_file" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$n_file"
# Recorded so a test can assert the probe identifies itself. Without this the
# marker could be dropped and every acceptance query would silently go back to
# counting the deployment's own retries as user traffic.
printf '%s\n' "$*" >> "$STUB_DIR/curl_args"
# The behaviour probe asks about three specific paths and must not consume the
# health sequence: those codes are about a different question, and letting the
# probe eat them made the health retry cases fail for a reason that had nothing
# to do with health.
case "$*" in
    *__deploy_probe_absent__*)
        printf '%s' "$(cat "$STUB_DIR/probe_ext_absent" 2>/dev/null || echo 404)"; exit 0 ;;
    *"/api/v1/ab/stats"*)
        printf '%s' "$(cat "$STUB_DIR/probe_ext_get_only" 2>/dev/null || echo 405)"; exit 0 ;;
    *"/api/v1/auth/login"*)
        printf '%s' "$(cat "$STUB_DIR/probe_ext_post" 2>/dev/null || echo 422)"; exit 0 ;;
    # The database health check reads the body of /api/v1/health. -fsS with no
    # -w or -o means it wants the body, not the status code. Separate from the
    # status-code path below, which uses -w '%{http_code}' -o /dev/null.
    *"-fsS"*"/api/v1/health"*)
        cat "$STUB_DIR/health_body" 2>/dev/null || echo '{"status":"healthy","checks":{"database":{"status":"healthy"},"models":{"status":"healthy"},"external_data":{"status":"healthy"}}}'
        exit 0 ;;
esac
total=$(wc -l < "$seq_file")
line=$(( n <= total ? n : total ))
code="$(sed -n "${line}p" "$seq_file")"
# curl prints 000 and exits non-zero when it cannot connect, which is exactly
# the case the caller has to handle without appending a second 000.
printf '%s' "$code"
[ "$code" = "000" ] && exit 7
exit 0
STUB
    # Frozen, so "two deployments in the same second" is a fact of the test
    # rather than a race against the wall clock. The earlier version waited for
    # the second to roll over, which made the case it cared about the one it was
    # least likely to exercise.
    cat > "$STUB_DIR/bin/date" <<'STUB'
#!/usr/bin/env bash
case "$*" in
    *%Y%m%dT%H%M%SZ*)      echo "20260803T120000Z" ;;
    *%Y-%m-%dT%H:%M:%SZ*)  echo "2026-08-03T12:00:00Z" ;;
    # `command date` is not an escape from this file. `command` bypasses
    # functions and aliases; it does not bypass PATH, and PATH begins with the
    # directory this stub lives in -- so it re-executed itself, forked
    # exponentially, and the runner killed the job with SIGTERM at 87 seconds.
    # Nothing reached this branch until --finalize asked for `date -d ... +%s`,
    # so the recursion sat here unfired for as long as the stub has existed.
    *)  real="$(PATH=/usr/bin:/bin command -v date)"
        [ -n "$real" ] || { echo "stub date: no system date found" >&2; exit 127; }
        exec "$real" "$@" ;;
esac
STUB
    cat > "$STUB_DIR/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
exit 1
STUB
    cat > "$STUB_DIR/bin/crontab" <<'STUB'
#!/usr/bin/env bash
exit 1
STUB
    chmod +x "$STUB_DIR/bin/"*
}

run_guard() {
    export STUB_DIR
    # shellcheck disable=SC2030
    #   The subshell is the point: the stubbed PATH must reach the script and
    #   nothing else, so that a later real command in this file cannot pick up a
    #   stub by accident. Losing the change on the way out is the intent.
    ( export PATH="$STUB_DIR/bin:$PATH"
      DEPLOY_REPO="$REPO" \
      COMPOSE_FILE="$REPO/compose.yml" \
      COMPOSE_PROJECT_NAME="p" \
      SITE_URL="http://stand.invalid" \
      MANIFEST_DIR="${MANIFEST_OVERRIDE:-$SANDBOX/manifests}" \
      DEPLOY_LOCK_DIR="${LOCK_DIR_OVERRIDE:-$SANDBOX/lockdir}" \
      HEALTH_ATTEMPTS="${HEALTH_ATTEMPTS:-3}" \
      HEALTH_DELAY=0 \
        bash "$SCRIPT" "$@" ) > "$SANDBOX/out" 2>&1
    RC=$?
}

echo "== the stub intercepts =="
# First, because every case below is worthless if the real docker is being run.
# In a subshell, like run_guard, so the stubbed PATH never leaks into this one.
new_sandbox
# shellcheck disable=SC2031
#   Same reason as run_guard above: the stubbed PATH is scoped to this one
#   command on purpose, so it cannot leak into the rest of the file.
check "the stubbed docker answers" \
    "$( export STUB_DIR; PATH="$STUB_DIR/bin:$PATH" docker compose config --services | tr '\n' ' ' )" \
    "nginx backend "
# And that a stub which does not handle a call reaches the real tool rather than
# itself. `date` freezes two formats and passes everything else through; it did
# so with `command date`, which bypasses functions and aliases but not PATH --
# and PATH begins with the stub. It re-executed itself without bound.
#
# The failure had no failing assertion. The job died at 87 seconds with SIGTERM
# and printed no verdict at all, which is why it is worth a case of its own:
# `timeout` turns the hang into an answer. The two frozen formats are asserted
# beside it, because a pass-through that forgot to freeze would be the other way
# to make this line green.
# shellcheck disable=SC2031
if [ "${ONLY_POSTGRES_WAIT_CASE:-0}" != "1" ]; then
    check "an unhandled date falls through to the system date, not to itself" \
        "$( export STUB_DIR; PATH="$STUB_DIR/bin:$PATH" timeout 10 date -u -d @86400 +%Y-%m-%d 2>/dev/null || echo "recursed or hung" )" \
        "1970-01-02"
    # shellcheck disable=SC2031
    check "and the run-id format is still frozen" \
        "$( export STUB_DIR; PATH="$STUB_DIR/bin:$PATH" date -u +%Y%m%dT%H%M%SZ )" \
        "20260803T120000Z"
fi
rm -rf "$SANDBOX"

echo "== PostgreSQL is healthy before the one-shot migrator =="
new_sandbox
run_guard
check "postgres start waits for health" "$RC" "0"
check "and compose receives the health wait flag" \
    "$(grep -qx 'compose -p p -f .*/compose.yml up -d --wait postgres' "$STUB_DIR/calls" && echo yes || echo no)" \
    "yes"
rm -rf "$SANDBOX"

# The focused red/green oracle runs only this scenario. The EXIT trap still
# reports any failed assertion, so an old `up -d postgres` invocation cannot
# turn a red test green by taking this early exit.
if [ "${ONLY_POSTGRES_WAIT_CASE:-0}" = "1" ]; then
    exit 0
fi

echo "== it refuses to deploy anything but current, clean main =="
new_sandbox
git -C "$REPO" checkout -qb sidebranch
run_guard
refused_because "a branch other than main" "production is deployed from main only"
rm -rf "$SANDBOX"

new_sandbox
echo stray > "$REPO/stray.txt"
run_guard
refused_because "a dirty working tree" "working tree is not clean"
rm -rf "$SANDBOX"

new_sandbox
# HEAD one commit behind what origin/main points at.
git -C "$REPO" reset -q --hard "$FIRST"
run_guard
refused_because "a HEAD that is not origin/main" "is not origin/main"
rm -rf "$SANDBOX"

echo "== rollback is allowed, but only to merged code =="
# The rule this replaces required HEAD == origin/main unconditionally, which
# forbade rolling back to the last good commit -- the guard would have blocked
# recovery during the incident it exists to prevent.
new_sandbox
run_guard --rollback "$FIRST"
check "an ancestor of origin/main is accepted" \
    "$(grep -c 'rolling back to' "$SANDBOX/out")" "1"
check "and it is not refused"  "$([ "$RC" = 0 ] && echo ran || echo "exit $RC")" "ran"
rm -rf "$SANDBOX"

new_sandbox
# A commit that exists but was never merged: reachable from no branch on origin.
git -C "$REPO" checkout -q -b unmerged
echo x > "$REPO/x.txt"
git -C "$REPO" add -A >/dev/null
git -C "$REPO" commit -qm unmerged
UNMERGED="$(git -C "$REPO" rev-parse HEAD)"
git -C "$REPO" checkout -q main
run_guard --rollback "$UNMERGED"
refused_because "an unmerged commit" "not an ancestor of origin/main"
rm -rf "$SANDBOX"

new_sandbox
run_guard --rollback deadbeefdeadbeefdeadbeefdeadbeefdeadbeef
refused_because "a commit that does not exist" "is not a commit in this repository"
rm -rf "$SANDBOX"

new_sandbox
run_guard --rollback
refused_because "--rollback without a commit" "needs a commit"
rm -rf "$SANDBOX"

new_sandbox
run_guard --rollback "$SECOND"
refused_because "rolling back to origin/main itself" "use a plain deploy"
rm -rf "$SANDBOX"

new_sandbox
run_guard --wat
refused_because "an unknown argument" "unknown argument"
rm -rf "$SANDBOX"

echo "== a target carrying .deploy-blocked is refused before anything changes =="
# Staged work can be merged before it may run: the ownership schema and routes
# are on main while the cutover that makes them safe is not. The marker is read
# from the target commit, for a deploy and for --rollback, and the refusal has
# to come before the checkout, the build, the migration and every container
# change -- "refused" after an `up` would be detection, not prevention.

# The docker calls that change something. Anything the guard runs before it
# decides is a read; none of these may appear.
mutating_calls() {
    # No file means docker was never called at all.
    [ -f "$STUB_DIR/calls" ] || { echo 0; return; }
    grep -cE 'compose .* (up|build|run|create|start|stop|restart|rm|down|pull)( |$)' \
        "$STUB_DIR/calls" || true
}
block_head() {
    echo "ownership cutover incomplete; requires PR2c" > "$REPO/.deploy-blocked"
    git -C "$REPO" add -A >/dev/null
    git -C "$REPO" commit -qm "blocked"
    git -C "$REPO" push -q origin main 2>/dev/null
    BLOCKED="$(git -C "$REPO" rev-parse HEAD)"
}

new_sandbox
block_head
run_guard
refused_because "a deploy of a commit carrying .deploy-blocked" "marked not deployable by .deploy-blocked"
check "the refusal quotes the marker's reason" \
    "$(grep -q 'requires PR2c' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and nothing was built, migrated or started" "$(mutating_calls)" "0"
check "and no deployment record was opened" \
    "$([ -e "$SANDBOX/manifests/in-progress" ] && echo opened || echo none)" "none"
rm -rf "$SANDBOX"

new_sandbox
block_head
# The marker removed in a later commit: that later commit is origin/main, and
# the blocked one is now an ordinary ancestor -- exactly what --rollback takes.
git -C "$REPO" rm -q .deploy-blocked
git -C "$REPO" commit -qm "unblocked"
git -C "$REPO" push -q origin main 2>/dev/null
UNBLOCKED="$(git -C "$REPO" rev-parse HEAD)"
run_guard --rollback "$BLOCKED"
refused_because "a rollback to a commit carrying .deploy-blocked" "marked not deployable by .deploy-blocked"
check "the checkout did not move" "$(git -C "$REPO" rev-parse HEAD)" "$UNBLOCKED"
check "and the migration did not run" \
    "$(cat "$STUB_DIR/migrate_calls" 2>/dev/null || echo 0)" "0"
check "and nothing was built or started" "$(mutating_calls)" "0"
rm -rf "$SANDBOX"

new_sandbox
block_head
git -C "$REPO" rm -q .deploy-blocked
git -C "$REPO" commit -qm "unblocked"
git -C "$REPO" push -q origin main 2>/dev/null
run_guard
# The removal condition: the commit that drops the marker deploys normally.
check "a commit without the marker is not refused for it" \
    "$(grep -c 'marked not deployable' "$SANDBOX/out" | tr -d ' ')" "0"
check "and it deploys" "$RC" "0"
# The control for the two zero counts above: on a deployment that does run,
# the same counter sees its calls. Without it a pattern that matched nothing
# would make "nothing was started" true of every run.
check "and the counter sees a real deployment's build and up" \
    "$([ "$(mutating_calls)" -gt 0 ] && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== it refuses a deployment that did not end where it should =="
new_sandbox
# A declared service that never came up.
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp" > "$STUB_DIR/running"
run_guard
refused_because "a declared service missing" "is not running"
rm -rf "$SANDBOX"

new_sandbox
# The converse, and the one that mattered: a container nothing declares,
# surviving beside the declared ones. This is the shape of the dev `app`
# container that served the public site for sixteen days.
echo "p-app-1|app|0.0.0.0:8000->8000/tcp" >> "$STUB_DIR/running"
run_guard
refused_because "an undeclared container surviving" "does not declare"
rm -rf "$SANDBOX"

echo "== it refuses anything reachable off-host but 80 and 443 =="
# Enumerated rather than sampled: a guard written against the one example that
# prompted it is how "8000:8000" got banned while "9000:8000" stayed legal.
for form in "0.0.0.0:8000->8000/tcp" "0.0.0.0:9000->8000/tcp" ":::8000->8000/tcp"; do
    new_sandbox
    echo "nginx" > "$STUB_DIR/services"
    echo "p-nginx-1|nginx|$form" > "$STUB_DIR/running"
    run_guard
    refused_because "published off-host: $form" "reachable off-host"
    rm -rf "$SANDBOX"
done
# Each of these states a form that must be *accepted*. The published 80 and 443
# come alongside it, because the guard now also requires them: a state where
# neither is published is not "a deployment with an extra loopback port", it is
# a deployment serving nothing, and it must be refused for that reason instead.
for form in "127.0.0.1:9090->9090/tcp" "0.0.0.0:80->80/tcp" "0.0.0.0:443->443/tcp" "8000/tcp"; do
    new_sandbox
    echo "nginx" > "$STUB_DIR/services"
    echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp, $form" > "$STUB_DIR/running"
    run_guard
    check "accepted: $form" "$([ "$RC" = 0 ] && echo ok || echo "refused: $(grep -m1 REFUSED "$SANDBOX/out")")" "ok"
    rm -rf "$SANDBOX"
done

echo "== it refuses an nginx that is not serving the repository's config =="
new_sandbox
echo "0000000000000000000000000000000000000000000000000000000000000000" > "$STUB_DIR/container_conf_sum"
run_guard
refused_because "a config hash that does not match" "not the repository's"
rm -rf "$SANDBOX"

new_sandbox
touch "$STUB_DIR/nginx_t_fails"
run_guard
refused_because "a config nginx itself rejects" "rejects its own configuration"
rm -rf "$SANDBOX"

new_sandbox
echo "    server app:8000 resolve;" > "$STUB_DIR/upstream_line"
run_guard
refused_because "an upstream that is not backend" "expected backend:8000"
rm -rf "$SANDBOX"

new_sandbox
# The host is right and the address would still be frozen at worker start.
# Accepting this is how the fix could be reverted while every deployment kept
# reporting success (#129).
echo "    server backend:8000;" > "$STUB_DIR/upstream_line"
run_guard
refused_because "an upstream without resolve" "has no 'resolve'"
rm -rf "$SANDBOX"

echo "== it refuses certificates from anywhere but the renewed store =="
# ./certs held a copy three weeks behind /etc/letsencrypt and on no renewal
# path. It would have served a valid certificate right up until it did not.
new_sandbox
echo "/opt/sora_earth_ai_platform/certs" > "$STUB_DIR/cert_source"
run_guard
refused_because "a certificate store that is not renewed" "not /etc/letsencrypt"
rm -rf "$SANDBOX"

echo "== health checks are retried, but not forever =="
new_sandbox
# Transient: nginx has just been recreated and the backend may still be taking
# its first connections, so one failure means nothing.
printf '000\n200\n' > "$STUB_DIR/http_code"
run_guard
check "a first failure is retried, not fatal"  "$RC" "0"
check "and the retry is reported"              "$(grep -c 'retrying' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

new_sandbox
# Permanent: every attempt fails and the deployment must end in an error rather
# than hang.
printf '000\n' > "$STUB_DIR/http_code"
run_guard
refused_because "a persistent failure" "after 3 attempts"
check "the status is not doubled" \
    "$(grep -c 'returned 000000' "$SANDBOX/out")" "0"
check "it reports the real status" \
    "$(grep -c 'returned 000 after' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

new_sandbox
echo 502 > "$STUB_DIR/http_code"
run_guard
refused_because "a non-200 from the site" "returned 502"
# The manifest is the next run's idea of what is deployed. Publishing one for a
# deployment whose smoke checks failed would name a state that was never
# reached, and the following rollback would aim at it.
check "no manifest is published when smoke fails" \
    "$(find "$SANDBOX/manifests" -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
check "and no partial file is left behind" \
    "$(find "$SANDBOX/manifests" -name '*.tmp' 2>/dev/null | wc -l | tr -d ' ')" "0"
rm -rf "$SANDBOX"

echo "== a good deployment is recorded =="
new_sandbox
run_guard
check "exit status is 0"          "$RC" "0"
check "a manifest was written"    "$(find "$SANDBOX/manifests" -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "1"
MAN="$SANDBOX/manifests/$(readlink "$SANDBOX/manifests/latest")"
check "it records the commit"     "$(grep -c "^commit         $SECOND" "$MAN")" "1"
# The champion the deployment is serving, from the health endpoint's
# model_provenance (PR C). Best-effort, so the stubbed curl (which returns no
# usable body) records `unknown` -- but the line is always present, which is
# what makes a manifest able to answer "which model was serving" at all.
check "it records the active model" "$(grep -c '^active_model   ' "$MAN")" "1"
check "it records the mode"       "$(grep -c '^mode           deploy' "$MAN")" "1"
check "it records the config hash" "$(grep -c "^nginx_config   sha256:$CONF_SUM" "$MAN")" "1"
# Without this a rollback has nothing to aim at: the manifest of the run that
# breaks production is the only record of what it replaced.
check "it records what it replaced" "$(grep -c 'state replaced by this run' "$MAN")" "1"
check "including the previous commit" "$(grep -c '^previous_commit ' "$MAN")" "1"
check "and the previous images"     "$(grep -c '^previous_images:' "$MAN")" "1"
# The systemctl stub exits 1, so this scenario is "the lookup failed", not "no
# renewal is configured". The previous assertion here demanded the words "no
# certbot timer" for it -- enshrining the conflation of the two. Production
# showed the cost on 2026-08-05: the deployment reported renewal missing while
# certbot.timer was enabled, active and had run ten hours earlier.
check "an unavailable systemctl is reported as undetermined, not absent" \
    "$(grep -qi 'could not query systemd' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and it does not claim the timer is missing" \
    "$(grep -qi 'no certbot timer and no cron' "$SANDBOX/out" && echo yes || echo no)" "no"
# The guard refuses a dirty tree, so a guard that dirties the tree refuses its
# own next run. The default manifest directory was inside the checkout and every
# test overrode it, which is precisely why nobody noticed.
check "the checkout is left clean" \
    "$(git -C "$REPO" status --porcelain | wc -l | tr -d ' ')" "0"
rm -rf "$SANDBOX"

echo "== and the checkout would not stay clean if manifests lived in it =="
# The control for the line above: with the manifest directory inside the
# checkout, the tree is dirty afterwards and the next deployment is refused.
# Without this the "left clean" assertion could pass for any reason at all.
new_sandbox
MANIFEST_OVERRIDE="$REPO/docs/evidence" run_guard
check "a manifest written into the checkout dirties it" \
    "$([ "$(git -C "$REPO" status --porcelain | wc -l)" -gt 0 ] && echo dirty || echo clean)" "dirty"
run_guard
refused_because "and the next run is then refused" "working tree is not clean"
rm -rf "$SANDBOX"

echo "== the manifest names what it replaced, not what it deployed =="
new_sandbox
run_guard
check "first deploy succeeds" "$RC" "0"
MAN1="$SANDBOX/manifests/$(readlink "$SANDBOX/manifests/latest")"
check "with no previous deployment recorded" \
    "$(grep -c '^previous_commit none-recorded' "$MAN1")" "1"

# A third commit, so the second deployment has a different target from the
# first. Deploying the same commit twice would make previous_commit and commit
# equal for an honest reason, and the assertion below could not tell a correct
# implementation from the bug it exists to catch -- both produce SECOND.
echo third > "$REPO/third.txt"
git -C "$REPO" add -A >/dev/null
git -C "$REPO" commit -qm third
git -C "$REPO" push -q origin main 2>/dev/null
THIRD="$(git -C "$REPO" rev-parse HEAD)"

sleep 1
run_guard
check "second deploy succeeds" "$RC" "0"
# Followed through the pointer, not chosen by sorting names -- the sorting is
# what broke, so a test that sorts would agree with the bug.
MAN2="$SANDBOX/manifests/$(readlink "$SANDBOX/manifests/latest")"
check "two manifests exist" \
    "$(find "$SANDBOX/manifests" -name '*.txt' | wc -l | tr -d ' ')" "2"
check "the second deployed the new commit" \
    "$(awk '/^commit /{print $2}' "$MAN2")" "$THIRD"
# The field a rollback depends on. It used to be `git rev-parse HEAD`, which on
# a deploy is already the target -- so it recorded the commit being deployed as
# the one being replaced, confidently and always wrongly. With two different
# commits the two answers finally differ.
check "and names the first deploy's commit as replaced" \
    "$(awk '/^previous_commit /{print $2}' "$MAN2")" "$SECOND"
rm -rf "$SANDBOX"

echo "== the lock must not sit anywhere others can write =="
# /var/lock is /run/lock, mode 1777. I checked the sticky bit, found it set, and
# reported that as sufficient. It is not: the sticky bit stops another user
# deleting root's lock file, not creating it first under its predictable name,
# and not planting a symlink there for `exec 9>` to follow and truncate as root.
#
# Enumerated over the mode space rather than sampled, since the check is
# arithmetic on two digits and it is easy to get the boundary wrong -- the first
# version of it did.
for mode in 700 750 755 711; do
    new_sandbox
    mkdir -p "$SANDBOX/lockdir"; chmod "$mode" "$SANDBOX/lockdir"
    LOCK_DIR_OVERRIDE="$SANDBOX/lockdir" run_guard
    check "mode $mode is accepted" \
        "$([ "$RC" = 0 ] && echo ok || echo "refused: $(grep -m1 REFUSED "$SANDBOX/out")")" "ok"
    rm -rf "$SANDBOX"
done
for mode in 770 757 777 720 702; do
    new_sandbox
    mkdir -p "$SANDBOX/lockdir"; chmod "$mode" "$SANDBOX/lockdir"
    LOCK_DIR_OVERRIDE="$SANDBOX/lockdir" run_guard
    refused_because "mode $mode is refused" "writable by group or others"
    rm -rf "$SANDBOX"
done

new_sandbox
# -d follows symlinks, so a link satisfies "is a directory" while the lock ends
# up somewhere else entirely -- possibly somewhere the attacker chose.
mkdir -p "$SANDBOX/real"; chmod 0750 "$SANDBOX/real"
ln -s "$SANDBOX/real" "$SANDBOX/linkdir"
LOCK_DIR_OVERRIDE="$SANDBOX/linkdir" run_guard
refused_because "a symlinked lock directory" "is a symlink"
rm -rf "$SANDBOX"

new_sandbox
# A directory someone else owns. They can replace the lock whatever its mode.
# Root can stage this directly; a non-root run borrows a system directory that
# root owns, which is the same property seen from the other side.
if [ "$(id -u)" = 0 ]; then
    mkdir -p "$SANDBOX/lockdir"; chmod 0750 "$SANDBOX/lockdir"
    chown 65534:65534 "$SANDBOX/lockdir" 2>/dev/null
    OTHER_DIR="$SANDBOX/lockdir"
else
    OTHER_DIR="/usr"     # root-owned, 0755
fi
LOCK_DIR_OVERRIDE="$OTHER_DIR" run_guard
refused_because "a lock directory owned by someone else" "not by the deploying user"
rm -rf "$SANDBOX"

echo "== deployments are serialised, and manifests are never overwritten =="
new_sandbox
# Contention must refuse, not skip. A backup that skips has lost nothing; a
# deployment that skips has silently not happened while its operator believes
# it did.
# The holder runs in the background; the assertion stays in this shell.
#
# It used to be the other way round -- guard and assertion both inside a
# subshell -- and the PASS/FAIL counters incremented there never reached the
# summary. A genuine failure printed FAIL and the run still ended "failed: 0".
# A test that can fail without changing the result is worse than no test.
#
# 0750 explicitly: mkdir uses the caller's umask, which is 002 for the CI
# runner, and the guard rightly refuses the 775 that produces. The lock path is
# the one the guard derives; it is no longer overridable, because an override
# let the file sit outside the directory all the checks validate.
mkdir -p "$SANDBOX/lockdir"; chmod 0750 "$SANDBOX/lockdir"
# The holder announces success; the test waits for that announcement.
#
# Polling `flock -n` and treating any non-zero result as "held" was wrong twice:
# flock returns non-zero for reasons other than contention, and after fifty
# failed probes the loop fell through and ran the guard anyway. If the holder
# never acquired the lock, the refusal under test would have been asserted
# against no contention at all -- passing or failing for reasons unrelated to
# the thing being checked.
( exec 9>"$SANDBOX/lockdir/deploy.lock"
  flock -n 9 || exit 1
  touch "$SANDBOX/lock-held"
  sleep 30 ) &
HOLDER=$!
HELD=0
for _ in $(seq 1 100); do
    if [ -f "$SANDBOX/lock-held" ]; then HELD=1; break; fi
    sleep 0.1
done
if [ "$HELD" != 1 ]; then
    bad "the lock holder never acquired the lock; contention was never tested"
else
    run_guard
    refused_because "a second deployment while one holds the lock" "another deployment holds"
fi
kill "$HOLDER" 2>/dev/null
wait "$HOLDER" 2>/dev/null
rm -rf "$SANDBOX"

new_sandbox
# The clock is frozen, so both of these land on the same timestamp. An earlier
# fix gave the second a "-1" suffix, and '-' sorts before '.', so picking the
# newest by name returned the *first* one -- the collision fix broke the chain it
# was written to protect. Nothing sorts names now; `latest` is followed.
#
# The assertion is deliberately about the chain rather than about files existing.
# The test this replaces checked that a new file appeared beside the old one,
# which stayed true while the chain was broken.
echo third > "$REPO/third.txt"
git -C "$REPO" add -A >/dev/null; git -C "$REPO" commit -qm third
git -C "$REPO" push -q origin main 2>/dev/null
THIRD="$(git -C "$REPO" rev-parse HEAD)"

git -C "$REPO" reset -q --hard "$SECOND"
git -C "$REPO" push -qf origin "HEAD:main" 2>/dev/null
run_guard
check "first deploy in this second succeeds"  "$RC" "0"
FIRST_MAN="$(readlink "$SANDBOX/manifests/latest")"

git -C "$REPO" reset -q --hard "$THIRD"
git -C "$REPO" push -qf origin "HEAD:main" 2>/dev/null
run_guard
check "second deploy in the same second succeeds" "$RC" "0"
SECOND_MAN="$(readlink "$SANDBOX/manifests/latest")"
check "they are different files"    "$([ "$FIRST_MAN" != "$SECOND_MAN" ] && echo different || echo same)" "different"
check "both manifests survive"      "$(find "$SANDBOX/manifests" -name '*.txt' | wc -l | tr -d ' ')" "2"
check "latest names the second"     "$(awk '/^commit /{print $2}' "$SANDBOX/manifests/$SECOND_MAN")" "$THIRD"
# The one that matters: the next run must read the second, not the first.
check "and the second names the first as replaced" \
    "$(awk '/^previous_commit /{print $2}' "$SANDBOX/manifests/$SECOND_MAN")" "$SECOND"
rm -rf "$SANDBOX"

new_sandbox
# A third deployment, still in the same frozen second, must see the second.
run_guard
echo third > "$REPO/third.txt"; git -C "$REPO" add -A >/dev/null
git -C "$REPO" commit -qm third; git -C "$REPO" push -q origin main 2>/dev/null
THIRD="$(git -C "$REPO" rev-parse HEAD)"
run_guard
echo fourth > "$REPO/fourth.txt"; git -C "$REPO" add -A >/dev/null
git -C "$REPO" commit -qm fourth; git -C "$REPO" push -q origin main 2>/dev/null
FOURTH="$(git -C "$REPO" rev-parse HEAD)"
run_guard
check "the third deploy succeeds"   "$RC" "0"
MAN3="$SANDBOX/manifests/$(readlink "$SANDBOX/manifests/latest")"
check "it deployed the newest commit"        "$(awk '/^commit /{print $2}' "$MAN3")" "$FOURTH"
check "and names the second deploy as previous" \
    "$(awk '/^previous_commit /{print $2}' "$MAN3")" "$THIRD"
rm -rf "$SANDBOX"

new_sandbox
# The pointer must win over any name in the directory.
#
# Without this the suite could not tell the pointer from sorting at all: with
# mktemp suffixes every manifest has the same shape, so picking the newest by
# name lands on the right file about half the time and a mutant that sorts
# survives. Confirmed -- one did, 66 tests green.
#
# The decoy sorts after every real manifest and names a commit that was never
# deployed. Anything that chooses by name reads it; anything that follows the
# pointer does not.
run_guard
REAL_PREV="$SECOND"
echo "commit deadbeefdeadbeefdeadbeefdeadbeefdeadbeef" > "$SANDBOX/manifests/zzzz-decoy.txt"
echo third > "$REPO/third.txt"; git -C "$REPO" add -A >/dev/null
git -C "$REPO" commit -qm third; git -C "$REPO" push -q origin main 2>/dev/null
run_guard
check "the deploy after a decoy succeeds" "$RC" "0"
MAND="$SANDBOX/manifests/$(readlink "$SANDBOX/manifests/latest")"
check "the decoy is ignored" \
    "$(awk '/^previous_commit /{print $2}' "$MAND")" "$REAL_PREV"
rm -rf "$SANDBOX"

new_sandbox
# A failed deployment must leave the pointer where it was: the previous
# deployment is still what is serving, and is still what a rollback aims at.
run_guard
GOOD_MAN="$(readlink "$SANDBOX/manifests/latest")"
echo 502 > "$STUB_DIR/http_code"
run_guard
refused_because "the second run fails smoke" "returned 502"
check "latest still names the good deployment" \
    "$(readlink "$SANDBOX/manifests/latest")" "$GOOD_MAN"
check "and no manifest was added"   "$(find "$SANDBOX/manifests" -name '*.txt' | wc -l | tr -d ' ')" "1"
rm -rf "$SANDBOX"

echo "== the readiness probe identifies itself =="
# nginx has just been recreated and the backend may still be accepting its first
# connections, so the probe is meant to start early, get a 502 and retry. That
# 502 lands in the same access log as user traffic. On release 4cd7232 the
# acceptance query counted it and reported a user-facing failure; there was
# none, and the release before reported zero only because the probe arrived
# after the backend was listening (#142).

new_sandbox
run_guard
check "the deploy succeeded" "$RC" "0"
# The denominator first: an empty file would make both sides of the next two
# comparisons 0 and they would pass having measured nothing.
check "the probe ran at all" \
    "$([ -s "$STUB_DIR/curl_args" ] && echo yes || echo no)" "yes"
check "every probe carries the marker" \
    "$(grep -c 'sora-deploy-healthcheck/1' "$STUB_DIR/curl_args")" \
    "$(wc -l < "$STUB_DIR/curl_args" | tr -d ' ')"
check "and the marker is not merely mentioned somewhere" \
    "$(grep -c -- '-A sora-deploy-healthcheck/1' "$STUB_DIR/curl_args")" \
    "$(wc -l < "$STUB_DIR/curl_args" | tr -d ' ')"
check "the run reports probe attempts separately" \
    "$(grep -c 'probe attempts' "$SANDBOX/out")" "1"
check "and says what acceptance must exclude" \
    "$(grep -c 'exclude only User-Agent' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

new_sandbox
# One 502 then 200 on each path: the shape a healthy deployment produces.
printf '502\n200\n200\n200\n' > "$STUB_DIR/http_code"
run_guard
check "an intermediate 502 does not fail the deployment" "$RC" "0"
check "and it is reported as a retry, not an outage" \
    "$(grep -c 'retrying' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

new_sandbox
# Never recovers. The final result is what decides, and a timeout is still a
# deployment failure -- marking the probe must not soften that.
printf '502\n' > "$STUB_DIR/http_code"
run_guard
refused_because "a probe that never reaches 200" "after"
rm -rf "$SANDBOX"

echo "== the rollback compares the journal against what is checked out =="
# The journal records deployments this script made. A deployment made by hand
# leaves no entry, so the next scripted run names the last *scripted* commit as
# the one it replaced -- skipping everything in between (#133). `--rollback`
# takes its target from that field via the "to undo:" line this script prints,
# so an operator following the script's own suggestion can land several commits
# further back than they expect, silently, at the moment they are least able to
# check.

new_sandbox
run_guard                                   # journal and checkout both SECOND
run_guard --rollback "$FIRST"
check "an agreeing journal does not block the rollback" "$RC" "0"
check "and it says what it compared" \
    "$(grep -c 'journal records' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

new_sandbox
run_guard                                   # journal records SECOND
# A deployment by hand: the checkout moves and the journal never hears about it.
git -C "$REPO" checkout -q --detach "$FIRST"
# The stub appends every docker invocation for the life of the sandbox, and the
# deployment above is a real one -- so counting from the start measures that,
# not the refusal. Truncated here so the window is the rollback attempt alone.
: > "$STUB_DIR/calls"
run_guard --rollback "$FIRST"
refused_because "a journal that disagrees with the checkout" \
    "disagrees with the checkout"
# Both sides of the comparison must appear, so an operator can see what the
# refusal is about without going to look. Asserted as "present", not as a
# count: the number of times each is printed is formatting, not contract.
check "the refusal names what is checked out" \
    "$(grep -qc "${FIRST:0:12}" "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and what the journal claims" \
    "$(grep -qc "${SECOND:0:12}" "$SANDBOX/out" && echo yes || echo no)" "yes"
# `grep -c` prints 0 and exits 1 on an empty file, so `|| echo 0` appended a
# second 0 and the comparison saw "0\n0". The earlier fix -- truncating the
# file -- is what exposed this: while it held stale calls the count was
# non-zero and the fallback never fired.
check "and nothing was deployed" \
    "$(grep -c 'up -d' "$STUB_DIR/calls" || true)" "0"
check "and the checkout did not move" \
    "$(git -C "$REPO" rev-parse HEAD)" "$FIRST"
rm -rf "$SANDBOX"

new_sandbox
run_guard
git -C "$REPO" checkout -q --detach "$FIRST"
ROLLBACK_ACKNOWLEDGE="$FIRST" run_guard --rollback "$FIRST"
check "an acknowledgement carrying the observed commit is accepted" "$RC" "0"
check "and it says so out loud" \
    "$(grep -c 'divergence acknowledged' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

new_sandbox
run_guard
git -C "$REPO" checkout -q --detach "$FIRST"
# The wrong SHA, and a plausible one: what the journal claims. An
# acknowledgement that could be pasted from the runbook without looking would
# be no acknowledgement at all.
ROLLBACK_ACKNOWLEDGE="$SECOND" run_guard --rollback "$FIRST"
refused_because "an acknowledgement of the wrong commit" "disagrees with the checkout"
rm -rf "$SANDBOX"

echo "== rollback reads the previous manifest, not the checkout =="
new_sandbox
run_guard                       # records SECOND as deployed
sleep 1
run_guard --rollback "$FIRST"   # rolls back to FIRST
check "the rollback succeeds"   "$RC" "0"
MANR="$SANDBOX/manifests/$(readlink "$SANDBOX/manifests/latest")"
check "it records the rollback target"  "$(awk '/^commit /{print $2}' "$MANR")" "$FIRST"
check "it records the mode"             "$(grep -c '^mode           rollback' "$MANR")" "1"
# Taken from the previous manifest. Read from the checkout it would have been
# FIRST, because the rollback checkout has already happened by that point.
check "and names the commit it replaced" "$(awk '/^previous_commit /{print $2}' "$MANR")" "$SECOND"
rm -rf "$SANDBOX"


echo "== a configuration that would publish off-host is refused before it starts =="
# Prevention, not detection. The runtime check below it sees the port only after
# `up`, and refusing then leaves it open -- see #71.
new_sandbox
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
cat > "$STUB_DIR/rendered" <<'JSON'
{"services": {
  "nginx": {"ports": [{"target": 80, "published": "80", "protocol": "tcp"},
                      {"target": 443, "published": "443", "protocol": "tcp"}]},
  "app":   {"ports": [{"target": 8000, "published": "8000", "protocol": "tcp"}]}
}}
JSON
run_guard
refused_because "a declared off-host 8000" "would publish"
check "and nothing was started" \
    "$(grep -qc 'up -d' "$STUB_DIR/calls" && echo started || echo no)" "no"
rm -rf "$SANDBOX"

echo "== the whole tuple decides, not the port number =="
# Each of these begins with an allowed number or looks close to one.
declared_refused() {
    local label="$1" spec="$2"
    new_sandbox
    echo "nginx" > "$STUB_DIR/services"
    echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
    printf '{"services": {"nginx": {"ports": [{"target": 80, "published": "80", "protocol": "tcp"}, {"target": 443, "published": "443", "protocol": "tcp"}, %s]}}}\n' \
        "$spec" > "$STUB_DIR/rendered"
    run_guard
    refused_because "$label" "would publish"
    rm -rf "$SANDBOX"
}
declared_refused "a range beginning at an allowed port" \
    '{"target": 80, "published": "80-81", "protocol": "tcp"}'
declared_refused "an allowed number over udp" \
    '{"target": 80, "published": "80", "protocol": "udp"}'
declared_refused "an allowed published with a different target" \
    '{"target": 8080, "published": "80", "protocol": "tcp"}'
declared_refused "a host address that is neither loopback nor all" \
    '{"host_ip": "10.0.0.5", "target": 80, "published": "80", "protocol": "tcp"}'
declared_refused "an unexpected publication mode" \
    '{"target": 80, "published": "80", "protocol": "tcp", "mode": "gateway"}'

echo "== loopback is allowed on both families =="
for ip in 127.0.0.1 ::1; do
    new_sandbox
    echo "nginx" > "$STUB_DIR/services"
    echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
    printf '{"services": {"nginx": {"ports": [{"target": 80, "published": "80", "protocol": "tcp"}, {"target": 443, "published": "443", "protocol": "tcp"}]}, "app": {"ports": [{"host_ip": "%s", "target": 8000, "published": "8000", "protocol": "tcp"}]}}}\n' \
        "$ip" > "$STUB_DIR/rendered"
    run_guard
    check "loopback $ip is accepted" \
        "$([ "$RC" = 0 ] && echo ok || echo "refused: $(grep -m1 REFUSED "$SANDBOX/out")")" "ok"
    rm -rf "$SANDBOX"
done

echo "== publishing nothing is not the same as publishing nothing forbidden =="
# The safety property is universally quantified, so an empty list satisfies it
# completely. Losing 80 costs every http:// caller, and the smoke test only
# exercises https://, so nothing else would notice.
# The runtime is made *correct* here on purpose: 80 and 443 are both published.
# Only the declaration is missing 80. Written the other way round the runtime
# check fires instead, the test passes, and the declared-side check it names is
# never exercised — removing that check failed nothing at all until this was
# split.
new_sandbox
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
printf '{"services": {"nginx": {"ports": [{"target": 443, "published": "443", "protocol": "tcp"}]}}}\n' \
    > "$STUB_DIR/rendered"
run_guard
refused_because "a configuration publishing no 80" "configuration publishes no off-host 80/tcp"
check "and it was refused before anything started" \
    "$(grep -qc 'up -d' "$STUB_DIR/calls" && echo started || echo no)" "no"
rm -rf "$SANDBOX"

new_sandbox
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|" > "$STUB_DIR/running"
run_guard
refused_because "a runtime publishing nothing at all" "no off-host 80/tcp"
rm -rf "$SANDBOX"


echo "== a decoy service cannot satisfy the completeness check =="
# `case "$name" in *nginx*)` matched any container whose name contained the
# substring. With nginx publishing nothing and a helper publishing 80 and 443,
# the gate reported the site as served while the service meant to serve it was
# not listening.
new_sandbox
printf 'nginx\nnginx-helper\n' > "$STUB_DIR/services"
{
    echo "p-nginx-1|nginx|"
    echo "p-nginx-helper-1|nginx-helper|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp"
} > "$STUB_DIR/running"
# Complete tuples, `mode` included, so the declaration passes the preflight
# cleanly and the refusal can only come from the runtime check.
cat > "$STUB_DIR/rendered" <<'JSON'
{"services": {
  "nginx":        {"ports": [{"target": 80, "published": "80", "protocol": "tcp", "mode": "ingress"},
                             {"target": 443, "published": "443", "protocol": "tcp", "mode": "ingress"}]},
  "nginx-helper": {"ports": []}
}}
JSON
run_guard
# The runtime wording, not the preflight's. Both say "no off-host 80/tcp", so
# matching that alone would pass on a preflight refusal and prove nothing about
# which containers the runtime check looked at.
refused_because "a helper publishing 80/443 does not stand in for nginx" \
    "nginx publishes no off-host 80/tcp"
check "the containers were started first" \
    "$(grep -qc 'up -d' "$STUB_DIR/calls" && echo yes || echo no)" "yes"
check "and the ports were read by compose service" \
    "$(grep -qc 'com.docker.compose.service"}}|{{.Ports' "$STUB_DIR/calls" && echo yes || echo no)" "yes"
echo "== a refusal after the containers are up rolls back =="
# Eight checks sit after `up`. Each used to abort a deployment whose containers
# were already running and leave the refused state in place -- the port case
# named an off-host port accurately and left it open.
with_previous_deployment() {
    mkdir -p "$SANDBOX/manifests"
    printf 'commit %s\n' "$FIRST" > "$SANDBOX/manifests/prev.txt"
    ln -sf prev.txt "$SANDBOX/manifests/latest"
}

new_sandbox
with_previous_deployment
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
touch "$STUB_DIR/nginx_t_fails"
run_guard
check "exit says the previous state is back"  "$RC" "1"
check "it rolled back"      "$(grep -qc 'rolling back' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "the recorded images were restored" \
    "$(grep -qc 'restored the images that were running' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "without building" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

new_sandbox
with_previous_deployment
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
touch "$STUB_DIR/nginx_t_fails"
echo t > "$STUB_DIR/users_table"
run_guard
# The previous commit predates persistent accounts and the migration has made
# the table: that code would not start on this schema and would ignore every
# block and reset, so the automatic rollback stops and says so, and the exit is
# the one that needs an operator.
check "an automatic rollback past persistent accounts is refused" "$RC" "76"
check "it says why" \
    "$(grep -qc 'ROLLBACK REFUSED' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and did not restore the previous images" \
    "$(grep -qc 'restored the images that were running' "$SANDBOX/out" && echo yes || echo no)" "no"
check "and the journal records the refusal" \
    "$(grep -qc 'rollback-refused' "$SANDBOX/manifests/in-progress" 2>/dev/null && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== a refusal with nothing recorded stops only what this run created =="
# No previous manifest: there is no state to restore, so the only safe action is
# to stop what this run started. Anything already present may belong to another
# project, and stopping it damages something this deployment does not own.
new_sandbox
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
echo "cid-preexisting" > "$STUB_DIR/pre_cids"
touch "$STUB_DIR/nginx_t_fails"
run_guard
check "a distinct exit for an incomplete rollback" "$RC" "76"
check "it says nothing was recorded" \
    "$(grep -qc 'no previous deployment recorded' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "the pre-existing container is not stopped" \
    "$(grep -c 'stop cid-preexisting' "$STUB_DIR/calls")" "0"
# And the other half: the container this run created *is* stopped. Without it
# the case cannot tell "correctly excluded" from "the stop path never ran".
check "the container this run created is stopped" \
    "$(grep -q 'stop cid-created-by-this-run' "$STUB_DIR/calls" && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== a failed rollback is its own outcome =="
# Distinguished from a successful one: "refused, previous state running" and
# "refused, and the rollback did not complete" call for opposite responses, and
# one non-zero status cannot say which happened.
new_sandbox
with_previous_deployment
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
touch "$STUB_DIR/nginx_t_fails"
# Both routes broken: the exact restore and the rebuild it falls back to.
# Breaking only the rebuild no longer says anything, because the rollback no
# longer goes that way first.
touch "$STUB_DIR/no_build_fails"
touch "$STUB_DIR/up_fails_after_first"
run_guard
check "exit says the rollback did not complete" "$RC" "76"
check "and it says so"  "$(grep -qc 'ROLLBACK FAILED' "$SANDBOX/out" && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== a refusal before the containers start does not roll back =="
# Nothing has changed yet, so rebuilding the previous state would be work done
# for no reason -- and would make a preflight refusal indistinguishable from a
# post-mutation one in the record.
new_sandbox
with_previous_deployment
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
cat > "$STUB_DIR/rendered" <<'JSON'
{"services": {
  "nginx": {"ports": [{"target": 80, "published": "80", "protocol": "tcp"},
                      {"target": 443, "published": "443", "protocol": "tcp"}]},
  "app":   {"ports": [{"target": 8000, "published": "8000", "protocol": "tcp"}]}
}}
JSON
run_guard
check "refused with the plain status" "$RC" "1"
check "and no rollback was attempted" \
    "$(grep -qc 'rolling back' "$SANDBOX/out" && echo yes || echo no)" "no"
rm -rf "$SANDBOX"


echo "== a rebuild is reported as an incomplete rollback, not a restore =="
# Rebuilding the previous commit usually produces a working deployment, and it
# is not the runtime state that was replaced: a base image can have moved, a
# dependency resolved differently. Reporting it as "restored" would hand the
# operator a claim nobody checked.
new_sandbox
with_previous_deployment
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
touch "$STUB_DIR/nginx_t_fails"
touch "$STUB_DIR/no_build_fails"
run_guard
check "a rebuild does not count as a restore" "$RC" "76"
check "and it says which one happened" \
    "$(grep -qc 'ROLLBACK INCOMPLETE' "$SANDBOX/out" && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== an image that does not match the snapshot is not a restore =="
# The `up` can succeed having started something else. Verified afterwards
# against the recorded ids rather than inferred from the exit status.
new_sandbox
with_previous_deployment
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
echo "sha256:something-else" > "$STUB_DIR/restored_image"
touch "$STUB_DIR/nginx_t_fails"
run_guard
check "a different image is not a restore" "$RC" "76"
check "and the mismatch is named" \
    "$(grep -qc 'do not match the snapshot' "$SANDBOX/out" && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== an unfinished journal stops the next run =="
# After a kill between `up` and the verdict, what is running is whatever that
# run left. Deploying on top of it would build on a state nobody accepted.
new_sandbox
mkdir -p "$SANDBOX/manifests"
printf 'run x\nstate mutating\n' > "$SANDBOX/manifests/in-progress"
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
run_guard
refused_because "an interrupted previous run" "did not finish"
check "and nothing was started" \
    "$(grep -qc 'up -d' "$STUB_DIR/calls" && echo started || echo no)" "no"
rm -rf "$SANDBOX"

echo "== a successful deployment leaves no journal behind =="
new_sandbox
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
run_guard
check "the deployment succeeded" "$RC" "0"
check "and the journal is closed" \
    "$( [ -f "$SANDBOX/manifests/in-progress" ] && echo left || echo closed )" "closed"
rm -rf "$SANDBOX"


echo "== a signal during the mutation rolls back =="
# Only `fail()` routed through the rollback, so a SIGTERM -- Ctrl-C, a dropped
# session, an orchestrator killing the run -- left the new containers running
# with nothing to undo them.
new_sandbox
with_previous_deployment
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
touch "$STUB_DIR/slow_up"
# shellcheck disable=SC2030,SC2031
#   The subshell is the point, as in run_guard: the stubbed PATH must reach the
#   guard and nothing else, and losing the change on the way out is the intent.
( export PATH="$STUB_DIR/bin:$PATH"
  # Exported here, not inherited. These blocks passed only because run_guard
  # had exported STUB_DIR earlier in the file; run either one alone, or under a
  # name filter, and every stub resolved it to the empty string and wrote to
  # paths like /calls.
  export STUB_DIR
  DEPLOY_REPO="$REPO" COMPOSE_FILE="$REPO/compose.yml" COMPOSE_PROJECT_NAME=p \
  SITE_URL="http://stand.invalid" MANIFEST_DIR="$SANDBOX/manifests" \
  DEPLOY_LOCK_DIR="$SANDBOX/lockdir" HEALTH_ATTEMPTS=3 HEALTH_DELAY=0 \
    bash "$SCRIPT" ) > "$SANDBOX/out" 2>&1 &
GUARD=$!
sleep 3
kill -TERM "$GUARD" 2>/dev/null
wait "$GUARD" 2>/dev/null
RC=$?
check "it reports the interruption" \
    "$(grep -qc 'interrupted by SIGTERM' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and rolled back rather than leaving it running" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== a run killed outright leaves its journal behind =="
# SIGKILL cannot be trapped, which is the case the journal exists for: what is
# running afterwards is whatever the interrupted run left, and the next run has
# to be able to see that.
new_sandbox
with_previous_deployment
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
touch "$STUB_DIR/slow_up"
# shellcheck disable=SC2030,SC2031
#   The subshell is the point, as in run_guard: the stubbed PATH must reach the
#   guard and nothing else, and losing the change on the way out is the intent.
( export PATH="$STUB_DIR/bin:$PATH"
  # Exported here, not inherited. These blocks passed only because run_guard
  # had exported STUB_DIR earlier in the file; run either one alone, or under a
  # name filter, and every stub resolved it to the empty string and wrote to
  # paths like /calls.
  export STUB_DIR
  DEPLOY_REPO="$REPO" COMPOSE_FILE="$REPO/compose.yml" COMPOSE_PROJECT_NAME=p \
  SITE_URL="http://stand.invalid" MANIFEST_DIR="$SANDBOX/manifests" \
  DEPLOY_LOCK_DIR="$SANDBOX/lockdir" HEALTH_ATTEMPTS=3 HEALTH_DELAY=0 \
    bash "$SCRIPT" ) > "$SANDBOX/out" 2>&1 &
GUARD=$!
sleep 3
kill -9 "$GUARD" 2>/dev/null
wait "$GUARD" 2>/dev/null
sleep 1
check "the journal survives the kill" \
    "$( [ -f "$SANDBOX/manifests/in-progress" ] && echo present || echo missing )" "present"
check "and records the phase it reached" \
    "$(grep -qc 'state          mutating' "$SANDBOX/manifests/in-progress" 2>/dev/null && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"


echo "== a post-mutation command failing outside fail() still rolls back =="
# Found by running the rollback against a real Docker daemon, not by these
# stubs: `UPSTREAM="$(... | grep server)"` returns non-zero when the pattern is
# absent, and `set -e` ended the script on the spot -- past `up`, before any
# verdict. No REFUSED was printed, the journal stayed at `mutating`, the new
# version kept serving, and the exit code was 1: the code that means "the
# previous state is running again".
#
# Eighteen commands after the mutation can fail that way. The exit trap covers
# all of them, including the ones nobody has thought of.
new_sandbox
with_previous_deployment
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
touch "$STUB_DIR/upstream_cmd_fails"
run_guard
check "it says what happened"  \
    "$(grep -q 'unexpected failure' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and rolled back rather than leaving the new version serving" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo yes || echo no)" "yes"
# Cleared, not left. A verified rollback puts the previous state back, so
# there is nothing for an operator to reconcile -- and a journal left behind
# would refuse the next deployment, making exit 1 and exit 76 the same thing in
# practice.
check "and leaves no journal to block the next run" \
    "$( [ -f "$SANDBOX/manifests/in-progress" ] && echo left || echo cleared )" "cleared"
rm -rf "$SANDBOX"


echo "== a rollback that cannot check out records why =="
# The manifest can name a commit this checkout no longer has -- a force-push, a
# pruned branch, a repository restored from elsewhere. Every other terminal path
# writes its outcome; this one left the journal reading `mutating`, which names
# the wrong phase and hides that the checkout is what broke.
new_sandbox
mkdir -p "$SANDBOX/manifests"
printf 'commit %s\n' "0000000000000000000000000000000000000000" > "$SANDBOX/manifests/prev.txt"
ln -sf prev.txt "$SANDBOX/manifests/latest"
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
touch "$STUB_DIR/nginx_t_fails"
run_guard
check "the rollback did not complete" "$RC" "76"
check "and it names the checkout" \
    "$(grep -q 'cannot check out' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "the journal says rollback-failed, not mutating" \
    "$(grep -q '^state          rollback-failed' "$SANDBOX/manifests/in-progress" 2>/dev/null && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== renewal: present, absent and undetermined are three states =="
# Undetermined is covered above, by the default stub that exits 1. These two
# are the states that stub can never produce, and without them "undetermined"
# could be the only branch this suite ever reaches -- which is how the previous
# assertion looked correct while the other two were untested.

new_sandbox
cat > "$STUB_DIR/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
cat <<'OUT'
NEXT                        LEFT  LAST                        PASSED UNIT          ACTIVATES
Wed 2026-08-05 21:12:48 UTC 5h    Wed 2026-08-05 05:05:04 UTC 10h    certbot.timer certbot.service
OUT
STUB
chmod +x "$STUB_DIR/bin/systemctl"
run_guard
check "a systemd timer is reported present" \
    "$(grep -q 'renewal mechanism is present: (systemd timer)' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and no absence is claimed" \
    "$(grep -qi 'no certbot timer and no cron' "$SANDBOX/out" && echo yes || echo no)" "no"
rm -rf "$SANDBOX"

new_sandbox
cat > "$STUB_DIR/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
echo "0 timers listed."
STUB
chmod +x "$STUB_DIR/bin/systemctl"
run_guard
# systemctl answered and certbot is genuinely not there. This is the only case
# in which claiming absence is supported by the evidence.
check "a successful lookup finding nothing warns of real absence" \
    "$(grep -qi 'no certbot timer and no cron' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and it is not reported as undetermined" \
    "$(grep -qi 'could not query systemd' "$SANDBOX/out" && echo yes || echo no)" "no"
rm -rf "$SANDBOX"

echo "== migrations are one step, run once, before anything is recreated =="
# #125. The application entrypoint used to run `alembic upgrade head`, and the
# backend and the scheduler share that file, so `up -d` started two migrators
# against one database at the same moment. These cases are about the three
# properties the replacement has: once, first, and fatal.

new_sandbox
run_guard
check "the migration step ran" \
    "$(cat "$STUB_DIR/migrate_calls" 2>/dev/null || echo 0)" "1"
# Exactly once, not at least once. Two migrators is the defect.
check "and no more than once" \
    "$(grep -cE 'run -d --no-deps .* migrate$' "$STUB_DIR/calls")" "1"
rm -rf "$SANDBOX"

new_sandbox
run_guard
# Line numbers in the recorded call log, so this is about order in time and not
# about the order the two greps happen to appear in.
MIG_LINE="$(grep -nE 'run -d --no-deps .* migrate$' "$STUB_DIR/calls" | head -1 | cut -d: -f1)"
UP_LINE="$(grep -n 'up -d --no-build --remove-orphans' "$STUB_DIR/calls" | head -1 | cut -d: -f1)"
check "the migration is recorded before the containers are recreated" \
    "$([ -n "$MIG_LINE" ] && [ -n "$UP_LINE" ] && [ "$MIG_LINE" -lt "$UP_LINE" ] && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

new_sandbox
touch "$STUB_DIR/migrate_fails"
run_guard
refused_because "a failed migration refuses the deployment" "alembic upgrade head failed"
# The point of moving it out of the entrypoint: a failure stops the deploy
# instead of restarting a container until it works.
check "and nothing was recreated" \
    "$(grep -c 'up -d --no-build --remove-orphans' "$STUB_DIR/calls")" "0"
# A manifest, not "any file". `in-progress` lives in the same directory and is
# supposed to be there: it is the journal saying the run was interrupted. The
# first version of this counted it and reported a manifest that was never
# written.
check "and no manifest was written" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
check "but the journal records the interruption" \
    "$([ -f "$SANDBOX/manifests/in-progress" ] && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

new_sandbox
touch "$STUB_DIR/postgres_fails"
run_guard
refused_because "a database that will not start refuses the deployment" "postgres would not start"
check "and the migration was not attempted against nothing" \
    "$(cat "$STUB_DIR/migrate_calls" 2>/dev/null || echo 0)" "0"
rm -rf "$SANDBOX"

echo "== the accounts are bootstrapped after the migration, before anything is recreated =="
# The new code reads accounts only from the `users` table. Serving it over an
# empty table is a site nobody can sign in to, and over a table with no active
# administrator one nobody can manage -- so the check runs every deployment,
# from the deployed image, and a refusal stops the release.

new_sandbox
run_guard
check "the account bootstrap ran exactly once" \
    "$(cat "$STUB_DIR/bootstrap_calls" 2>/dev/null || echo 0)" "1"
MIG_LINE="$(grep -nE 'run -d --no-deps .* migrate$' "$STUB_DIR/calls" | head -1 | cut -d: -f1)"
BOOT_LINE="$(grep -n 'run --rm --no-deps bootstrap-users' "$STUB_DIR/calls" | head -1 | cut -d: -f1)"
UP_LINE="$(grep -n 'up -d --no-build --remove-orphans' "$STUB_DIR/calls" | head -1 | cut -d: -f1)"
check "after the migration and before the containers are recreated" \
    "$([ -n "$MIG_LINE" ] && [ -n "$BOOT_LINE" ] && [ -n "$UP_LINE" ] && [ "$MIG_LINE" -lt "$BOOT_LINE" ] && [ "$BOOT_LINE" -lt "$UP_LINE" ] && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

new_sandbox
touch "$STUB_DIR/bootstrap_fails"
run_guard
refused_because "a refused account bootstrap refuses the deployment" "account bootstrap refused"
check "and nothing was recreated" \
    "$(grep -c 'up -d --no-build --remove-orphans' "$STUB_DIR/calls")" "0"
rm -rf "$SANDBOX"

new_sandbox
touch "$STUB_DIR/migrate_fails"
run_guard
check "a failed migration is not followed by a bootstrap" \
    "$(cat "$STUB_DIR/bootstrap_calls" 2>/dev/null || echo 0)" "0"
rm -rf "$SANDBOX"

echo "== code from before persistent accounts is not restored once users exists =="
# That code would refuse the migrated schema and ignore every block and reset.
# The sandbox's commits have no app/accounts.py, so every rollback here is to
# such code.

new_sandbox
echo t > "$STUB_DIR/users_table"
run_guard --rollback "$FIRST"
refused_because "an explicit rollback onto a schema with users is refused" "predates persistent accounts"
check "and the migration did not run" \
    "$(cat "$STUB_DIR/migrate_calls" 2>/dev/null || echo 0)" "0"
rm -rf "$SANDBOX"

new_sandbox
touch "$STUB_DIR/users_table_unreadable"
run_guard --rollback "$FIRST"
refused_because "an unreadable answer refuses rather than assumes the table is absent" "predates persistent accounts"
rm -rf "$SANDBOX"

new_sandbox
echo f > "$STUB_DIR/users_table"
run_guard --rollback "$FIRST"
check "with no users table, the rollback is not refused for it" \
    "$(grep -c 'predates persistent accounts' "$SANDBOX/out" | tr -d ' ')" "0"
rm -rf "$SANDBOX"

new_sandbox
git -C "$REPO" rm -q scripts/bootstrap_users.py
git -C "$REPO" commit -qm "no bootstrap"
git -C "$REPO" push -q origin main 2>/dev/null
run_guard
check "code without the bootstrap script does not ask for the service" \
    "$(cat "$STUB_DIR/bootstrap_calls" 2>/dev/null || echo 0)" "0"
rm -rf "$SANDBOX"

echo "== the cutover: build, stop, confirm, one migrator, bootstrap, forced start =="
# Project ownership changes what the old code may do: its INSERT is refused by
# the new schema and its reads ignore the scope. So the old backend and
# scheduler are stopped -- and confirmed stopped -- before the one migrator
# runs, and the first start afterwards is a forced recreate from the pinned
# image, never an `up` that could start the stopped old containers again.

# The line number of the first recorded call matching an extended regex, or
# nothing.
call_line() { grep -nE -- "$1" "$STUB_DIR/calls" 2>/dev/null | head -1 | cut -d: -f1; }
# "a < b < c ...", for line numbers that must all be present.
in_order() {
    local prev="" n
    for n in "$@"; do
        [ -n "$n" ] || { echo "missing"; return; }
        if [ -n "$prev" ] && [ "$prev" -ge "$n" ]; then echo "out of order: $*"; return; fi
        prev="$n"
    done
    echo "in order"
}
L_BUILD='build backend$'
L_STOP_NGINX=' stop nginx$'
L_STOP_APPS=' stop -t 60 backend scheduler$'
L_MIGRATE='run -d --no-deps .* migrate$'
L_BOOT='run --rm --no-deps bootstrap-users$'
L_FORCE='up -d --no-build --no-deps --force-recreate backend scheduler$'
L_UP='up -d --no-build --remove-orphans$'
L_NGINX='up -d --force-recreate nginx$'

new_sandbox
run_guard
check "a cutover deployment succeeds" "$RC" "0"
check "build, stop nginx, stop the applications, migrate, bootstrap, forced start, the rest, nginx" \
    "$(in_order "$(call_line "$L_BUILD")" "$(call_line "$L_STOP_NGINX")" "$(call_line "$L_STOP_APPS")" \
        "$(call_line "$L_MIGRATE")" "$(call_line "$L_BOOT")" "$(call_line "$L_FORCE")" \
        "$(call_line "$L_UP")" "$(call_line "$L_NGINX")")" "in order"
check "the migrator runs exactly once" \
    "$(grep -cE -- "$L_MIGRATE" "$STUB_DIR/calls")" "1"
check "under a name tied to this run, which is how a deadline or a signal finds it" \
    "$(grep -cE -- 'run -d --no-deps -T --name p-migrate-20260803T120000Z-[0-9]+ -e PGAPPNAME=p-migrate-20260803T120000Z-[0-9]+ migrate$' "$STUB_DIR/calls")" "1"
check "the applications are asked whether they stopped, by label, replicas included" \
    "$(grep -c 'label=com.docker.compose.service=backend --format {{.ID}} {{.State}}' "$STUB_DIR/calls")" "1"
check "and the database is asked whether transactions are still open before migrating" \
    "$(in_order "$(call_line "$L_STOP_APPS")" "$(call_line "'active:'")" "$(call_line "$L_MIGRATE")")" "in order"
# The window the plan names: a generic `up` between the stop and the forced
# start can start the stopped old containers on the new schema. Every `up`
# there must be a --no-deps start that names neither application nor nginx.
#
# Checked by name. The first version of this check discarded every
# `up --no-deps` line unread, so `up --no-deps backend` -- a revival by name --
# passed it.
reviving_ups() {
    local s f line w
    s="$(call_line "$L_STOP_APPS")"; f="$(call_line "$L_FORCE")"
    if [ -z "$s" ] || [ -z "$f" ]; then echo "the stop or the forced start is missing"; return; fi
    sed -n "$((s + 1)),$((f - 1))p" "$STUB_DIR/calls" | grep -E ' up( |$)' | while IFS= read -r line; do
        case " $line " in *" --no-deps "*) ;; *) echo "$line"; continue ;; esac
        for w in ${line##* up }; do
            case "$w" in backend|scheduler|nginx) echo "$line"; break ;; esac
        done
    done
}
check "no up between the stop and the forced start could revive an old container" \
    "$(reviving_ups | tr '\n' '|')" ""
check "the new backend checks the schema head" \
    "$(in_order "$(call_line "$L_FORCE")" "$(call_line 'verify_schema_head.py')" "$(call_line "$L_UP")")" "in order"
check "the cutover stages are reported" \
    "$(grep -c '  cutover: ' "$SANDBOX/out")" "8"
check "and the build is not one of them: it changes nothing that serves" \
    "$(grep -c 'cutover: building' "$SANDBOX/out")" "0"
rm -rf "$SANDBOX"

echo "== a build or a database that will not start changes nothing, so undoes nothing =="
# PHASE=mutating is set at the first stop, not before the build: a refusal in
# the build or the PostgreSQL wait has nothing to roll back, and "rolling back"
# from there restored and restarted containers nobody had touched.
for _f in build_fails postgres_fails; do
    new_sandbox
    with_previous_deployment
    touch "$STUB_DIR/$_f"
    run_guard
    check "$_f: refused with exit 1" "$RC" "1"
    check "$_f: no rollback was attempted" "$(grep -c '== rolling back ==' "$SANDBOX/out")" "0"
    check "$_f: no image was restored" "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "not"
    check "$_f: nothing was stopped" "$(grep -cE ' stop ' "$STUB_DIR/calls")" "0"
    check "$_f: no deployment journal was opened" \
        "$([ -e "$SANDBOX/manifests/in-progress" ] && echo opened || echo none)" "none"
    rm -rf "$SANDBOX"
done

echo "== the budgets are numbers =="
for _bad in DEPLOY_STOP_TIMEOUT=abc DEPLOY_DRAIN_SECONDS=3s DEPLOY_MIGRATE_DEADLINE=08 \
            DEPLOY_MIGRATOR_GONE_SECONDS=0 DEPLOY_MIGRATOR_CREATE_SECONDS=-1 \
            DEPLOY_MIGRATE_DEADLINE=1e3 DEPLOY_STOP_TIMEOUT=999999 HEALTH_ATTEMPTS=x; do
    new_sandbox
    # shellcheck disable=SC2163
    #   The name and the value come from the list: exporting "$_bad" is the point.
    ( export "$_bad"; run_guard; echo "$RC" > "$SANDBOX/rc" )
    RC="$(cat "$SANDBOX/rc")"
    refused_because "a budget of ${_bad}" "${_bad%%=*}"
    check "  ${_bad}: refused before anything was read or changed" "$(mutating_calls)" "0"
    rm -rf "$SANDBOX"
done
new_sandbox
DEPLOY_STOP_TIMEOUT=5 DEPLOY_DRAIN_SECONDS=1 DEPLOY_MIGRATE_DEADLINE=30 HEALTH_DELAY=0 run_guard
check "plain decimal budgets are accepted" "$RC" "0"
check "  and the stop uses the one given" "$(grep -c ' stop -t 5 backend scheduler$' "$STUB_DIR/calls")" "1"
rm -rf "$SANDBOX"

echo "== the image is pinned by id, from the build to the last up =="
# A tag is a pointer. The deployment read the id once after the build; if the
# tag moves after that -- another build, a pull, a retag -- every command that
# resolved the tag again would run a different image than the one migrated
# from. The pin override names the id; the stub resolves the tag at each
# command unless the command carries it.
new_sandbox
echo "sha256:moved-after-the-build" > "$STUB_DIR/tag_moves_after_build"
mkdir -p "$SANDBOX/tmp"
TMPDIR="$SANDBOX/tmp" run_guard
check "a tag moved after the build: the deployment still succeeds" "$RC" "0"
check "  the tag did move" "$(cat "$STUB_DIR/tag_moved" 2>/dev/null)" "sha256:moved-after-the-build"
check "  the migrator ran the built image" "$(awk '$1 == "migrate" {print $2}' "$STUB_DIR/ran_images")" "sha256:image"
check "  the bootstrap ran the built image" "$(awk '$1 == "bootstrap-users" {print $2}' "$STUB_DIR/ran_images")" "sha256:image"
check "  backend started from the built image" "$(awk '$1 == "backend" {print $2}' "$STUB_DIR/ran_images")" "sha256:image"
check "  scheduler started from the built image" "$(awk '$1 == "scheduler" {print $2}' "$STUB_DIR/ran_images")" "sha256:image"
check "  no later up moved backend onto the tag" \
    "$(grep -c 'unpinned up recreated backend' "$STUB_DIR/migration_log" 2>/dev/null)" "0"
check "  every run and up after the build carried the pin" \
    "$(sed -n "$(call_line "$L_BUILD"),\$p" "$STUB_DIR/calls" | grep -E ' (run|up) ' \
        | grep -v -e 'up -d --wait postgres' -e sora-image-restore | grep -vc sora-image-pin)" "0"
check "  the override was used, from this run's temporary directory" \
    "$(grep -c "sora-image-pin" "$STUB_DIR/calls" | awk '{print ($1 > 0) ? "used" : "unused"}')" "used"
check "  and removed afterwards" "$(find "$SANDBOX/tmp" -name 'sora-image-pin.*' | wc -l | tr -d ' ')" "0"
rm -rf "$SANDBOX"

new_sandbox
# A refusal after the build leaves the journal, and it names the pinned id.
touch "$STUB_DIR/migrate_fails"
mkdir -p "$SANDBOX/tmp"
TMPDIR="$SANDBOX/tmp" run_guard
check "a refused run's journal records the pinned image" \
    "$(grep -c '^image_pin      sha256:image$' "$SANDBOX/manifests/in-progress" 2>/dev/null)" "1"
check "  and the override is removed on that path too" "$(find "$SANDBOX/tmp" -name 'sora-image-pin.*' | wc -l | tr -d ' ')" "0"
rm -rf "$SANDBOX"

new_sandbox
# An override for a service the configuration does not declare creates it.
printf '%s\n' nginx backend > "$STUB_DIR/all_services"
run_guard
check "the pin names only declared services" "$RC" "0"
check "  backend is pinned" "$(grep -c 'pinned backend to' "$SANDBOX/out")" "1"
check "  an undeclared migrate is not" "$(grep -c 'pinned migrate' "$SANDBOX/out")" "0"
rm -rf "$SANDBOX"

new_sandbox
echo "sha256:bad id" > "$STUB_DIR/app_image_id"
run_guard
refused_because "a built id that is not an image id is not pinned" "is not an image id"
check "  and nothing was stopped" "$(grep -cE ' stop ' "$STUB_DIR/calls")" "0"
rm -rf "$SANDBOX"

# With dependencies declared, as production declares them.
new_sandbox
cat > "$STUB_DIR/rendered" <<'JSON'
{"services": {
  "nginx":     {"ports": [{"target": 80,  "published": "80",  "protocol": "tcp", "mode": "ingress"},
                          {"target": 443, "published": "443", "protocol": "tcp", "mode": "ingress"}],
                "depends_on": {"backend": {"condition": "service_started"}}},
  "postgres":  {},
  "pgbouncer": {"depends_on": {"postgres": {"condition": "service_healthy"}}},
  "redis":     {},
  "prometheus": {"depends_on": ["backend"]},
  "backend":   {"environment": {"SORA_ENV": "production"},
                "depends_on": {"pgbouncer": {"condition": "service_started"}, "redis": {"condition": "service_started"}}},
  "scheduler": {"environment": {"SORA_ENV": "production"},
                "depends_on": {"pgbouncer": {"condition": "service_started"}, "redis": {"condition": "service_started"}}}
}}
JSON
run_guard
check "with dependencies: the deployment succeeds" "$RC" "0"
check "their dependencies, transitively and nothing that needs them, start on their own" \
    "$(sed 's/.*--no-deps //' "$STUB_DIR/deps_started" 2>/dev/null)" "pgbouncer postgres redis"
check "after the bootstrap and before the forced start" \
    "$(in_order "$(call_line "$L_BOOT")" "$(call_line 'up -d --no-build --no-deps pgbouncer')" "$(call_line "$L_FORCE")")" "in order"
rm -rf "$SANDBOX"

echo "== no migration unless the old code is confirmed stopped =="
# Each of these refuses before the migrator and the bootstrap, and never
# reaches the forced start of the new code.
refused_before_migrating() {
    local label="$1"
    check "$label: the migrator was not run" "$(cat "$STUB_DIR/migrate_calls" 2>/dev/null || echo 0)" "0"
    check "$label: nor the bootstrap" "$(cat "$STUB_DIR/bootstrap_calls" 2>/dev/null || echo 0)" "0"
    check "$label: nor was the new code started" "$(grep -cE -- "$L_FORCE" "$STUB_DIR/calls")" "0"
}

new_sandbox
touch "$STUB_DIR/app_stop_fails"
run_guard
refused_because "a stop that fails" "backend and scheduler would not stop"
refused_before_migrating "a stop that fails"
rm -rf "$SANDBOX"

new_sandbox
touch "$STUB_DIR/nginx_stop_fails"
run_guard
refused_because "nginx that will not stop" "nginx would not stop"
refused_before_migrating "nginx that will not stop"
check "and the applications were not stopped behind a running nginx" \
    "$(grep -cE -- "$L_STOP_APPS" "$STUB_DIR/calls")" "0"
rm -rf "$SANDBOX"

new_sandbox
# `compose stop` returned 0 and a container is still up: a replica compose did
# not stop, or one started by hand.
printf 'cid-backend-1 exited\ncid-backend-2 running\n' > "$STUB_DIR/state_backend"
run_guard
refused_because "a replica still running after the stop" "still running, or their state cannot be read"
check "and it is named" "$(grep -q 'backend:cid-backend-2:running' "$SANDBOX/out" && echo named || echo unnamed)" "named"
refused_before_migrating "a replica still running"
# Nothing to restore here, and the replica is still up: "STOPPED" would be a
# description of what was intended, not of what docker reports.
check "  STOPPED is not claimed over the running replica" \
    "$(grep -c 'STOPPED and the site is not serving' "$SANDBOX/out")" "0"
check "  the operator is told the state is not confirmed" \
    "$(grep -c 'could NOT be confirmed stopped' "$SANDBOX/out")" "1"
check "  and the journal says unknown" \
    "$(grep -c '^apps           unknown' "$SANDBOX/manifests/in-progress" 2>/dev/null)" "1"
rm -rf "$SANDBOX"

new_sandbox
echo "cid-scheduler restarting" > "$STUB_DIR/state_scheduler"
run_guard
refused_because "a scheduler restarting after the stop" "still running, or their state cannot be read"
refused_before_migrating "a restarting scheduler"
rm -rf "$SANDBOX"

new_sandbox
touch "$STUB_DIR/app_state_unreadable"
run_guard
refused_because "a container state that cannot be read" "still running, or their state cannot be read"
refused_before_migrating "an unreadable state"
rm -rf "$SANDBOX"

new_sandbox
echo 3 > "$STUB_DIR/active_sessions"
DEPLOY_DRAIN_SECONDS=2 run_guard
refused_because "transactions still open after the drain" "client transactions were still open 2s"
refused_before_migrating "open transactions"
rm -rf "$SANDBOX"

new_sandbox
touch "$STUB_DIR/active_unreadable"
run_guard
refused_because "a transaction count that cannot be read" "could not be asked whether transactions are open"
refused_before_migrating "an unreadable transaction count"
rm -rf "$SANDBOX"

# The answer the drain wants, printed by a psql that then failed. `|| true`
# kept the text and lost the status, so this was "quiet".
new_sandbox
echo 0 > "$STUB_DIR/active_sessions"
echo 2 > "$STUB_DIR/active_exit"
run_guard
refused_because "active:0 with a non-zero exit" "could not be asked whether transactions are open"
check "  the answer is quoted with its status" "$(grep -c "answer '(exit 2) active:0'" "$SANDBOX/out")" "1"
refused_before_migrating "active:0 with a non-zero exit"
rm -rf "$SANDBOX"

for _answer in "" "active:" "active:-1" "garbage" "$(printf 'active:0\nactive:0')"; do
    new_sandbox
    printf '%s' "$_answer" > "$STUB_DIR/active_text"
    run_guard
    refused_because "a drain answer [$(printf '%s' "$_answer" | tr '\n' '|')] refuses" "could not be asked whether transactions are open"
    refused_before_migrating "drain answer [$(printf '%s' "$_answer" | tr '\n' '|')]"
    rm -rf "$SANDBOX"
done

echo "== a failed migration or bootstrap never starts the applications =="
new_sandbox
with_previous_deployment
touch "$STUB_DIR/migrate_fails"
run_guard
refused_because "a failed migration" "alembic upgrade head failed"
check "the new code was not started" "$(grep -cE -- "$L_FORCE" "$STUB_DIR/calls")" "0"
# The restore spells the same `up` with a second -f, its own override.
check "no generic up ran in the deployment" \
    "$(grep -E -- "$L_UP" "$STUB_DIR/calls" | grep -vc -- 'sora-image-restore')" "0"
check "no new manifest was published" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' ! -name prev.txt | wc -l | tr -d ' ')" "0"
check "the schema did not change, so the previous images came back" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "restored"
check "and the migrator was confirmed gone before the schema was read" \
    "$(in_order "$(call_line 'name=\^/p-migrate-')" "$(call_line "'ownership:'")")" "in order"
rm -rf "$SANDBOX"

new_sandbox
# No previous deployment: nothing to restore. The journal must say the
# applications are stopped, because they are.
touch "$STUB_DIR/migrate_fails"
run_guard
check "a failed first migration is an incomplete rollback" "$RC" "76"
check "the journal says the applications are stopped" \
    "$(grep -c '^apps           stopped' "$SANDBOX/manifests/in-progress" 2>/dev/null)" "1"
check "and at which stage" \
    "$(grep -c '^cutover        migrating' "$SANDBOX/manifests/in-progress" 2>/dev/null)" "1"
check "and the operator is told the site is not serving" \
    "$(grep -c 'STOPPED and the site is not serving' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

echo "== code from before project ownership is not restored onto its schema =="
# The old target in these cases HAS the accounts code. Without it the accounts
# barrier would refuse first and hide whether this one exists at all.
commit_files() {
    local f
    for f in "$@"; do
        mkdir -p "$REPO/$(dirname "$f")"
        echo "# stand-in" > "$REPO/$f"
    done
    git -C "$REPO" add -A >/dev/null
    git -C "$REPO" commit -qm "add $*"
    git -C "$REPO" push -q origin main 2>/dev/null
    git -C "$REPO" rev-parse HEAD
}
OWNERSHIP_FILES="app/project_access.py app/services/evaluation_store.py alembic/versions/b7d41f2e9c35_project_ownership_and_access_audit.py"
# ACCOUNTS: persistent accounts, no ownership -- the release before ownership.
# A later commit on top, so ACCOUNTS is an ordinary --rollback target.
accounts_target() {
    ACCOUNTS="$(commit_files app/accounts.py)"
    LATEST="$(commit_files later.txt)"
}
previous_is() {
    mkdir -p "$SANDBOX/manifests"
    printf 'commit %s\n' "$1" > "$SANDBOX/manifests/prev.txt"
    ln -sf prev.txt "$SANDBOX/manifests/latest"
}

echo "== the undo suggestion is asked of the rollback barriers =="
# Control: nothing bars going back, so the suggestion is the rollback itself.
new_sandbox
previous_is "$FIRST"
run_guard
check "control: a deploy over an unbarred previous succeeds" "$RC" "0"
check "  and suggests --rollback to it" \
    "$(grep -c "^to undo:  .* --rollback $FIRST\$" "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

# The previous release predates accounts and the users table exists: the
# accounts barrier would refuse, so the suggestion must say fix forward.
new_sandbox
previous_is "$FIRST"
echo t > "$STUB_DIR/users_table"
run_guard
check "a previous release the accounts barrier refuses: the deploy still succeeds" "$RC" "0"
check "  no --rollback is suggested" "$(grep -c '^to undo:  .*--rollback' "$SANDBOX/out")" "0"
check "  it says why and what to do" \
    "$(grep -c '^to undo:  no rollback -- .* predates persistent accounts' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

# The previous release has accounts but not ownership, and the schema has it.
new_sandbox
accounts_target
previous_is "$ACCOUNTS"
echo evaluations.owner_user_id > "$STUB_DIR/schema"
run_guard
check "a previous release the ownership barrier refuses: the deploy still succeeds" "$RC" "0"
check "  no --rollback is suggested" "$(grep -c '^to undo:  .*--rollback' "$SANDBOX/out")" "0"
check "  it says why" \
    "$(grep -c '^to undo:  no rollback -- .* predates project ownership' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

for item in evaluations.owner_user_id evaluations.data_scope \
            predictions_log.owner_user_id predictions_log.data_scope \
            table:project_access_events table:project_access_targets; do
    new_sandbox
    accounts_target
    echo "$item" > "$STUB_DIR/schema"
    run_guard --rollback "$ACCOUNTS"
    refused_because "explicit rollback with only $item present" "has no project ownership"
    check "  $item: not refused by the accounts barrier instead" \
        "$(grep -c 'predates persistent accounts' "$SANDBOX/out")" "0"
    check "  $item: the checkout did not move" "$(git -C "$REPO" rev-parse HEAD)" "$LATEST"
    check "  $item: nothing was built, stopped or started" "$(mutating_calls)" "0"
    rm -rf "$SANDBOX"

    new_sandbox
    accounts_target
    previous_is "$ACCOUNTS"
    echo "$item" > "$STUB_DIR/schema"
    touch "$STUB_DIR/nginx_t_fails"
    run_guard
    check "automatic rollback with only $item present is refused" "$RC" "76"
    check "  $item: rollback-refused in the journal" \
        "$(grep -c '^state          rollback-refused' "$SANDBOX/manifests/in-progress" 2>/dev/null)" "1"
    check "  $item: the previous images were not restored" \
        "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "not"
    check "  $item: the checkout stayed on the new code" \
        "$(git -C "$REPO" rev-parse HEAD)" "$LATEST"
    rm -rf "$SANDBOX"
done

# The answer must be exactly `ownership:<n>`; anything else refuses.
for answer in "" "ownership:" "garbage" "ownership:x" "$(printf 'ownership:0\nownership:0')"; do
    new_sandbox
    accounts_target
    printf '%s' "$answer" > "$STUB_DIR/ownership_answer"
    run_guard --rollback "$ACCOUNTS"
    refused_because "an unexpected probe answer [$(printf '%s' "$answer" | tr '\n' '|')] refuses" "has no project ownership"
    check "  and the checkout did not move" "$(git -C "$REPO" rev-parse HEAD)" "$LATEST"
    rm -rf "$SANDBOX"
done

# And the same answers in the automatic rollback, which decides with the same
# probe from inside abort_deployment.
for answer in "" "ownership:" "garbage" "ownership:x" "$(printf 'ownership:0\nownership:0')"; do
    new_sandbox
    accounts_target
    previous_is "$ACCOUNTS"
    printf '%s' "$answer" > "$STUB_DIR/ownership_answer"
    touch "$STUB_DIR/nginx_t_fails"
    run_guard
    _a="$(printf '%s' "$answer" | tr '\n' '|')"
    check "automatic rollback, probe answer [$_a]: refused" "$RC" "76"
    check "  [$_a]: rollback-refused" \
        "$(grep -c '^state          rollback-refused' "$SANDBOX/manifests/in-progress" 2>/dev/null)" "1"
    check "  [$_a]: no old image restored" "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "not"
    check "  [$_a]: no fallback build" "$(grep -c 'up -d --build' "$STUB_DIR/calls")" "0"
    rm -rf "$SANDBOX"
done

# The exact answer that allows -- `ownership:0` -- printed by a psql that then
# exited non-zero. Reproduced by review: `pg_scalar ... || true` let it through.
new_sandbox
accounts_target
echo "ownership:0" > "$STUB_DIR/ownership_answer"
echo 1 > "$STUB_DIR/ownership_exit"
run_guard --rollback "$ACCOUNTS"
refused_because "explicit rollback: ownership:0 with exit 1 refuses" "has no project ownership"
check "  the refusal quotes the status" "$(grep -c "probe answered '(exit 1) ownership:0'" "$SANDBOX/out")" "1"
check "  the checkout did not move" "$(git -C "$REPO" rev-parse HEAD)" "$LATEST"
check "  nothing was built, stopped or started" "$(mutating_calls)" "0"
rm -rf "$SANDBOX"

new_sandbox
accounts_target
previous_is "$ACCOUNTS"
echo "ownership:0" > "$STUB_DIR/ownership_answer"
echo 1 > "$STUB_DIR/ownership_exit"
touch "$STUB_DIR/nginx_t_fails"
run_guard
check "automatic rollback: ownership:0 with exit 1 is refused" "$RC" "76"
check "  no old image restored" "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "not"
rm -rf "$SANDBOX"

# The accounts barrier's question, the same way: `users:false` from a psql
# that failed is not "the table is absent".
new_sandbox
echo f > "$STUB_DIR/users_table"
echo 1 > "$STUB_DIR/users_exit"
run_guard --rollback "$FIRST"
refused_because "explicit rollback: users:false with exit 1 refuses" "predates persistent accounts"
check "  nothing was built, stopped or started" "$(mutating_calls)" "0"
rm -rf "$SANDBOX"

# Each of the three capability files absent on its own. A target missing any
# one of them is from the middle of the change and the schema decides; the
# first version of this case removed only the migration.
for _missing in $OWNERSHIP_FILES; do
    new_sandbox
    _have=""
    for _f in $OWNERSHIP_FILES; do [ "$_f" = "$_missing" ] || _have="$_have $_f"; done
    # shellcheck disable=SC2086
    #   Deliberately split: two paths.
    PARTIAL="$(commit_files app/accounts.py $_have)"
    LATEST="$(commit_files later.txt)"
    echo "predictions_log.owner_user_id" > "$STUB_DIR/schema"
    run_guard --rollback "$PARTIAL"
    refused_because "a target without $_missing" "has no project ownership"
    check "  without $_missing: the probe was asked" "$(grep -c "'ownership:'" "$STUB_DIR/calls")" "1"
    check "  without $_missing: the checkout did not move" "$(git -C "$REPO" rev-parse HEAD)" "$LATEST"
    rm -rf "$SANDBOX"
done

new_sandbox
accounts_target
touch "$STUB_DIR/ownership_unreadable"
run_guard --rollback "$ACCOUNTS"
refused_because "an explicit rollback when the schema cannot be read" "has no project ownership"
check "  and nothing was built, stopped or started" "$(mutating_calls)" "0"
rm -rf "$SANDBOX"

new_sandbox
accounts_target
previous_is "$ACCOUNTS"
touch "$STUB_DIR/ownership_unreadable"
touch "$STUB_DIR/nginx_t_fails"
run_guard
check "an automatic rollback when the schema cannot be read is refused" "$RC" "76"
check "  without restoring the old images" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "not"
check "  and without a fallback build" "$(grep -c 'up -d --build' "$STUB_DIR/calls")" "0"
rm -rf "$SANDBOX"

echo "== a rollback the schema allows still happens =="
new_sandbox
accounts_target
run_guard --rollback "$ACCOUNTS"
check "no ownership schema: the explicit rollback runs" "$RC" "0"
check "  and it checked out the target" "$(git -C "$REPO" rev-parse HEAD)" "$ACCOUNTS"
check "  the probe was asked" "$(grep -c "'ownership:'" "$STUB_DIR/calls")" "1"
rm -rf "$SANDBOX"

new_sandbox
# shellcheck disable=SC2086
#   Deliberately split: three paths.
OWNED="$(commit_files app/accounts.py $OWNERSHIP_FILES)"
LATEST="$(commit_files later.txt)"
printf '%s\n' evaluations.owner_user_id evaluations.data_scope > "$STUB_DIR/schema"
run_guard --rollback "$OWNED"
check "an ownership-capable target is allowed onto the ownership schema" "$RC" "0"
check "  without asking the database" "$(grep -c "'ownership:'" "$STUB_DIR/calls")" "0"
rm -rf "$SANDBOX"

new_sandbox
# Only two of the three files: a commit from the middle of the change is not
# ownership-capable, and the schema decides.
PARTIAL="$(commit_files app/accounts.py app/project_access.py app/services/evaluation_store.py)"
LATEST="$(commit_files later.txt)"
echo "evaluations.data_scope" > "$STUB_DIR/schema"
run_guard --rollback "$PARTIAL"
refused_because "a target with the routes but not the migration" "has no project ownership"
rm -rf "$SANDBOX"

new_sandbox
# shellcheck disable=SC2086
OWNED="$(commit_files app/accounts.py $OWNERSHIP_FILES)"
LATEST="$(commit_files later.txt)"
previous_is "$OWNED"
printf '%s\n' evaluations.owner_user_id > "$STUB_DIR/schema"
touch "$STUB_DIR/nginx_t_fails"
run_guard
check "an automatic rollback to ownership-capable code restores it" "$RC" "1"
check "  the recorded images came back" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "restored"
rm -rf "$SANDBOX"

echo "== the barrier after the migration commits =="
new_sandbox
accounts_target
previous_is "$ACCOUNTS"
touch "$STUB_DIR/migration_adds_ownership"
touch "$STUB_DIR/bootstrap_fails"
run_guard
check "a bootstrap refused after the ownership migration committed" "$RC" "76"
check "  rollback-refused" \
    "$(grep -c '^state          rollback-refused' "$SANDBOX/manifests/in-progress" 2>/dev/null)" "1"
check "  no old image was restored" "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "not"
check "  no old image was rebuilt" "$(grep -c 'up -d --build' "$STUB_DIR/calls")" "0"
check "  the checkout did not move" "$(git -C "$REPO" rev-parse HEAD)" "$LATEST"
check "  the new code was never started either" "$(grep -cE -- "$L_FORCE" "$STUB_DIR/calls")" "0"
check "  and the operator is told everything is stopped" \
    "$(grep -c 'STOPPED and the site is not serving' "$SANDBOX/out")" "1"
check "  not that the new code is running" \
    "$(grep -c 'new backend and scheduler are running' "$SANDBOX/out")" "0"
rm -rf "$SANDBOX"

new_sandbox
accounts_target
previous_is "$ACCOUNTS"
touch "$STUB_DIR/migration_adds_ownership"
touch "$STUB_DIR/nginx_t_fails"
run_guard
check "a refusal after the new code started, onto the migrated schema" "$RC" "76"
check "  says the new code is running" \
    "$(grep -c 'new backend and scheduler are running' "$SANDBOX/out")" "1"
check "  and restored nothing" "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "not"
rm -rf "$SANDBOX"

# The accounts barrier stays independent: a pre-accounts target is refused for
# accounts even where the ownership probe would allow it.
new_sandbox
echo t > "$STUB_DIR/users_table"
run_guard --rollback "$FIRST"
refused_because "a pre-accounts target is still refused by the accounts barrier" "predates persistent accounts"
rm -rf "$SANDBOX"

echo "== the migrator is stopped, and gone, before the schema is read =="
run_guard_bg() {
    export STUB_DIR
    # shellcheck disable=SC2030,SC2031
    ( export PATH="$STUB_DIR/bin:$PATH"
      DEPLOY_REPO="$REPO" COMPOSE_FILE="$REPO/compose.yml" COMPOSE_PROJECT_NAME=p \
      SITE_URL="http://stand.invalid" MANIFEST_DIR="$SANDBOX/manifests" \
      DEPLOY_LOCK_DIR="$SANDBOX/lockdir" HEALTH_ATTEMPTS=3 HEALTH_DELAY=0 \
        bash "$SCRIPT" ) > "$SANDBOX/out" 2>&1 &
    GUARD=$!
}
# Waits for a line in a file, up to ten seconds.
wait_for_line() {
    local i=0
    while [ "$i" -lt 100 ]; do
        grep -q -- "$2" "$1" 2>/dev/null && return 0
        sleep 0.1; i=$((i + 1))
    done
    return 1
}

new_sandbox
accounts_target
previous_is "$ACCOUNTS"
touch "$STUB_DIR/migration_adds_ownership"
echo 8 > "$STUB_DIR/slow_migrate"
run_guard_bg
wait_for_line "$STUB_DIR/calls" ' migrate$'
sleep 1
kill -TERM "$GUARD" 2>/dev/null
wait "$GUARD" 2>/dev/null
RC=$?
check "a signal during the migration is reported" "$(grep -c 'interrupted by SIGTERM' "$SANDBOX/out")" "1"
check "  the migrator was stopped before the schema was read" \
    "$(grep -n -e migrator-stopped -e ownership-probe "$STUB_DIR/migration_log" 2>/dev/null | head -1 | cut -d: -f2)" "migrator-stopped"
check "  its transaction never committed" "$(grep -c committed "$STUB_DIR/migration_log")" "0"
check "  and with the schema unchanged the previous images came back" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "restored"
check "  only after the migrator was gone" \
    "$(in_order "$(call_line 'stop -t 10 cidmigrator')" "$(call_line "'ownership:'")" "$(call_line 'up -d --no-build --remove-orphans$')")" "in order"
check "  and the new code was never started" "$(grep -cE -- "$L_FORCE" "$STUB_DIR/calls")" "0"
rm -rf "$SANDBOX"

new_sandbox
accounts_target
previous_is "$ACCOUNTS"
touch "$STUB_DIR/migration_adds_ownership"
echo 8 > "$STUB_DIR/slow_after_commit"
run_guard_bg
wait_for_line "$STUB_DIR/migration_log" committed
kill -TERM "$GUARD" 2>/dev/null
wait "$GUARD" 2>/dev/null
RC=$?
check "a signal after the migration committed is a refused rollback" "$RC" "76"
check "  rollback-refused" \
    "$(grep -c '^state          rollback-refused' "$SANDBOX/manifests/in-progress" 2>/dev/null)" "1"
check "  no old image was restored" "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "not"
check "  the applications stay stopped" "$(grep -c 'STOPPED and the site is not serving' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

new_sandbox
accounts_target
previous_is "$ACCOUNTS"
touch "$STUB_DIR/migration_adds_ownership"
echo 8 > "$STUB_DIR/slow_migrate"
DEPLOY_MIGRATE_DEADLINE=2 run_guard
refused_because "a migration past its deadline" "did not finish within 2s"
check "  the migrator was stopped before the schema was read" \
    "$(grep -n -e migrator-stopped -e ownership-probe "$STUB_DIR/migration_log" 2>/dev/null | head -1 | cut -d: -f2)" "migrator-stopped"
check "  its transaction never committed" "$(grep -c committed "$STUB_DIR/migration_log")" "0"
check "  and the previous images came back, the schema being unchanged" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "restored"
rm -rf "$SANDBOX"

new_sandbox
accounts_target
previous_is "$ACCOUNTS"
echo 8 > "$STUB_DIR/slow_migrate"
touch "$STUB_DIR/migrator_ps_unreadable"
DEPLOY_MIGRATE_DEADLINE=2 DEPLOY_MIGRATOR_GONE_SECONDS=2 run_guard
check "a migrator that cannot be confirmed gone refuses the rollback" "$RC" "76"
check "  rollback-refused" \
    "$(grep -c '^state          rollback-refused' "$SANDBOX/manifests/in-progress" 2>/dev/null)" "1"
check "  the schema was not read at all" "$(grep -c "'ownership:'" "$STUB_DIR/calls")" "0"
check "  nothing old was restored" "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "not"
rm -rf "$SANDBOX"

new_sandbox
accounts_target
previous_is "$ACCOUNTS"
echo 8 > "$STUB_DIR/slow_migrate"
touch "$STUB_DIR/migrator_session_unreadable"
DEPLOY_MIGRATE_DEADLINE=2 run_guard
check "a migrator session that cannot be asked about refuses the rollback" "$RC" "76"
check "  the schema was not read" "$(grep -c "'ownership:'" "$STUB_DIR/calls")" "0"
check "  nothing old was restored" "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "not"
rm -rf "$SANDBOX"

new_sandbox
accounts_target
previous_is "$ACCOUNTS"
echo 8 > "$STUB_DIR/slow_migrate"
echo 1 > "$STUB_DIR/active_after_migrate"
DEPLOY_MIGRATE_DEADLINE=2 DEPLOY_DRAIN_SECONDS=2 run_guard
check "a transaction still open after the migrator stopped refuses the rollback" "$RC" "76"
check "  the schema was not read" "$(grep -c "'ownership:'" "$STUB_DIR/calls")" "0"
check "  nothing old was restored" "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "not"
rm -rf "$SANDBOX"

# The line of the first occurrence of each marker in migration_log, in order.
log_order() {
    local m n out=""
    for m in "$@"; do
        n="$(grep -nx -- "$m" "$STUB_DIR/migration_log" 2>/dev/null | head -1 | cut -d: -f1)"
        out="$out ${n:-}"
    done
    # shellcheck disable=SC2086
    #   Deliberately split into in_order's arguments; an empty one is "missing".
    in_order $out
}
wait_for_file() {
    local i=0
    while [ "$i" -lt 150 ]; do [ -e "$1" ] && return 0; sleep 0.1; i=$((i + 1)); done
    return 1
}

echo "== a signal while the migrator is being created =="
# A signal between `compose run` starting and the daemon registering the
# container. The earlier design killed the client and then believed a few empty
# `docker ps` answers -- a fixed settle window -- and a registration that came
# later than the window was missed and committed after the schema was read. The
# client is now never killed: the rollback waits for it to return, and only
# then looks, removes and confirms by id.

# The passing control: the client returns two seconds after the signal; the
# container it created is found, removed and confirmed gone before the probe,
# and with the schema unchanged the previous release comes back.
new_sandbox
accounts_target
previous_is "$ACCOUNTS"
touch "$STUB_DIR/migration_adds_ownership"
echo 2 > "$STUB_DIR/migrate_create_delay"
echo 8 > "$STUB_DIR/slow_migrate"
run_guard_bg
wait_for_line "$STUB_DIR/calls" ' migrate$'
kill -TERM "$GUARD" 2>/dev/null
wait "$GUARD" 2>/dev/null
RC=$?
check "control, signal during the create: the signal is reported" "$(grep -c 'interrupted by SIGTERM' "$SANDBOX/out")" "1"
check "  the rollback waited for the creating client, then removed what it made, before the schema was read" \
    "$(log_order registered migrator-stopped migrator-removed ownership-probe)" "in order"
check "  the container was confirmed gone by id" \
    "$(in_order "$(call_line 'rm -f cidmigrator')" "$(grep -n 'inspect -f {{.Id}} cidmigrator' "$STUB_DIR/calls" | tail -1 | cut -d: -f1)" "$(call_line "'ownership:'")")" "in order"
sleep 3
check "  its transaction never committed" "$(grep -c '^committed$' "$STUB_DIR/migration_log")" "0"
check "  and with the schema unchanged the previous images came back" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "restored"
check "  exit 1: the previous state is serving" "$RC" "1"
rm -rf "$SANDBOX"

# The negative: the registration comes later than the creating client's whole
# budget -- later than any window a check could wait out. Nothing may be
# decided: no probe, no restore, every rollback forbidden. And the
# registration does then happen and commit, which is why.
new_sandbox
accounts_target
previous_is "$ACCOUNTS"
touch "$STUB_DIR/migration_adds_ownership"
echo 6 > "$STUB_DIR/migrate_create_delay"
DEPLOY_MIGRATOR_CREATE_SECONDS=2 run_guard_bg
wait_for_line "$STUB_DIR/calls" ' migrate$'
kill -TERM "$GUARD" 2>/dev/null
wait "$GUARD" 2>/dev/null
RC=$?
check "negative, registration after the budget, with a signal: refused" "$RC" "76"
check "  the schema was not read" "$(grep -c "'ownership:'" "$STUB_DIR/calls")" "0"
check "  nothing old was restored or rebuilt" \
    "$([ -f "$STUB_DIR/rolled_back" ] || grep -q 'up -d --build' "$STUB_DIR/calls" && echo restored || echo not)" "not"
check "  the journal says rollback-refused" \
    "$(grep -c '^state          rollback-refused' "$SANDBOX/manifests/in-progress" 2>/dev/null)" "1"
check "  and the operator is told why" "$(grep -c 'ROLLBACK REFUSED: whether the migrator' "$SANDBOX/out")" "1"
wait_for_line "$STUB_DIR/migration_log" '^committed$'
check "  the late registration did commit afterwards -- the doubt was real" \
    "$(grep -c '^committed$' "$STUB_DIR/migration_log")" "1"
rm -rf "$SANDBOX"

# The same without a signal: the creating client simply outlives its budget.
new_sandbox
accounts_target
previous_is "$ACCOUNTS"
touch "$STUB_DIR/migration_adds_ownership"
echo 6 > "$STUB_DIR/migrate_create_delay"
DEPLOY_MIGRATOR_CREATE_SECONDS=2 run_guard
check "negative, registration after the budget, no signal: refused" "$RC" "76"
check "  the schema was not read" "$(grep -c "'ownership:'" "$STUB_DIR/calls")" "0"
check "  nothing old was restored" "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "not"
check "  the new code was never started" "$(grep -cE -- "$L_FORCE" "$STUB_DIR/calls")" "0"
wait_for_line "$STUB_DIR/migration_log" '^committed$'
rm -rf "$SANDBOX"

# The creating client returning is not enough: what it returned has to name an
# object. A client that exited non-zero after a lost response, with no
# container docker can name, says nothing about whether the daemon accepted
# the create -- and in this case it had, and the container lands later.
new_sandbox
accounts_target
previous_is "$ACCOUNTS"
touch "$STUB_DIR/migration_adds_ownership"
echo 3 > "$STUB_DIR/migrate_create_lost"
run_guard
check "negative, the create's response lost (client exit 1, nothing named): refused" "$RC" "76"
check "  the schema was not read" "$(grep -c "'ownership:'" "$STUB_DIR/calls")" "0"
check "  nothing old was restored" "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "not"
check "  the journal says rollback-refused" \
    "$(grep -c '^state          rollback-refused' "$SANDBOX/manifests/in-progress" 2>/dev/null)" "1"
check "  and why: the migrator is unknown" \
    "$(grep -c '^detail         migrator unknown' "$SANDBOX/manifests/in-progress" 2>/dev/null)" "1"
wait_for_line "$STUB_DIR/migration_log" '^committed$'
check "  the accepted create did land and commit afterwards -- the doubt was real" \
    "$(grep -c '^committed$' "$STUB_DIR/migration_log")" "1"
rm -rf "$SANDBOX"

# The control beside it: the client exits non-zero, but docker names the
# container it made -- a known object. It is removed, confirmed gone by id,
# and with the schema unchanged the previous release comes back.
new_sandbox
accounts_target
previous_is "$ACCOUNTS"
touch "$STUB_DIR/migrate_start_fails"
run_guard
check "control, created but the start failed: the previous release comes back" "$RC" "1"
check "  the container was removed and confirmed gone before the probe" \
    "$(in_order "$(call_line 'rm -f cidmigrator')" "$(grep -n 'inspect -f {{.Id}} cidmigrator' "$STUB_DIR/calls" | tail -1 | cut -d: -f1)" "$(call_line "'ownership:'")")" "in order"
check "  the refusal named the failed creation" "$(grep -c 'could not be created, or its creation could not be confirmed' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

# Removal confirmed positively: docker saying "No such object" for the id. An
# id docker cannot answer about is not gone.
new_sandbox
accounts_target
previous_is "$ACCOUNTS"
touch "$STUB_DIR/migration_adds_ownership"
echo 8 > "$STUB_DIR/slow_migrate"
touch "$STUB_DIR/migrator_inspect_by_id_fails"
DEPLOY_MIGRATE_DEADLINE=2 DEPLOY_MIGRATOR_GONE_SECONDS=2 run_guard
check "a removal docker cannot confirm refuses the rollback" "$RC" "76"
check "  the schema was not read" "$(grep -c "'ownership:'" "$STUB_DIR/calls")" "0"
check "  nothing old was restored" "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "not"
rm -rf "$SANDBOX"

# Docker 29 writes "error: no such object: <id>". That is the same positive
# answer, and a deployment must go through on it. Before this case the guard
# matched only "No such", and the first production cutover refused here with
# the migration committed and every service stopped.
new_sandbox
accounts_target
previous_is "$ACCOUNTS"
touch "$STUB_DIR/docker29_messages"
run_guard
check "docker 29's lower-case 'no such object' confirms the migrator gone" "$RC" "0"
check "  and no answer was read as unanswered" \
    "$(grep -c 'docker cannot say whether' "$SANDBOX/out")" "0"
rm -rf "$SANDBOX"

# The id is known and removable, but the second question -- does anything
# else carry this run's name -- cannot be answered. Unanswered is not "no".
# (The earlier name-lookup case could not isolate this: its fault also hid the
# id, so the identification refused first and this guard was never reached.)
new_sandbox
accounts_target
previous_is "$ACCOUNTS"
touch "$STUB_DIR/migration_adds_ownership"
echo 8 > "$STUB_DIR/slow_migrate"
touch "$STUB_DIR/migrator_name_ps_fails"
DEPLOY_MIGRATE_DEADLINE=2 run_guard
check "a name lookup docker cannot answer refuses the rollback" "$RC" "76"
check "  the schema was not read" "$(grep -c "'ownership:'" "$STUB_DIR/calls")" "0"
check "  nothing old was restored" "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "not"
rm -rf "$SANDBOX"

echo "== the migrator's server-side session is terminated until it is gone =="
new_sandbox
accounts_target
previous_is "$ACCOUNTS"
echo 8 > "$STUB_DIR/slow_migrate"
echo 2 > "$STUB_DIR/migrator_sessions"
DEPLOY_MIGRATE_DEADLINE=2 run_guard
check "a session that survives two terminations: the rollback proceeds once it is gone" "$RC" "1"
check "  terminated twice" "$(grep -c '^migrator-session-terminated$' "$STUB_DIR/migration_log")" "2"
check "  and the schema was read only after it was confirmed gone" \
    "$(log_order migrator-session-terminated migrator-session-gone ownership-probe)" "in order"
rm -rf "$SANDBOX"

new_sandbox
accounts_target
previous_is "$ACCOUNTS"
echo 8 > "$STUB_DIR/slow_migrate"
echo 999 > "$STUB_DIR/migrator_sessions"
DEPLOY_MIGRATE_DEADLINE=2 DEPLOY_MIGRATOR_GONE_SECONDS=2 run_guard
check "a session that will not die refuses the rollback" "$RC" "76"
check "  the schema was not read" "$(grep -c "'ownership:'" "$STUB_DIR/calls")" "0"
check "  nothing old was restored" "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "not"
rm -rf "$SANDBOX"

new_sandbox
accounts_target
previous_is "$ACCOUNTS"
echo 8 > "$STUB_DIR/slow_migrate"
echo 1 > "$STUB_DIR/migrator_exit"
DEPLOY_MIGRATE_DEADLINE=2 run_guard
check "migrator:0 from a psql that failed refuses the rollback" "$RC" "76"
check "  the schema was not read" "$(grep -c "'ownership:'" "$STUB_DIR/calls")" "0"
rm -rf "$SANDBOX"

echo "== a start that fails partway: stopped, and the state said is the state observed =="
new_sandbox
accounts_target
previous_is "$ACCOUNTS"
touch "$STUB_DIR/migration_adds_ownership"
touch "$STUB_DIR/app_recreate_partial"
run_guard
check "backend started and scheduler did not, onto the ownership schema: refused" "$RC" "76"
check "  backend, which had started, is stopped again" "$(cat "$STUB_DIR/live_backend" 2>/dev/null)" "exited"
check "  after the partial start" "$(log_order partial-start ownership-probe apps-stopped-after-start)" "in order"
check "  the journal says stopped" \
    "$(grep -c '^apps           stopped' "$SANDBOX/manifests/in-progress" 2>/dev/null)" "1"
check "  the operator is told STOPPED" "$(grep -c 'STOPPED and the site is not serving' "$SANDBOX/out")" "1"
check "  and not that the new code runs" "$(grep -c 'new backend and scheduler are running' "$SANDBOX/out")" "0"
check "  no old image was restored" "$([ -f "$STUB_DIR/rolled_back" ] && echo restored || echo not)" "not"
rm -rf "$SANDBOX"

new_sandbox
accounts_target
previous_is "$ACCOUNTS"
touch "$STUB_DIR/migration_adds_ownership"
touch "$STUB_DIR/app_recreate_partial"
touch "$STUB_DIR/restop_fails"
run_guard
check "a partial start that cannot be stopped again: refused" "$RC" "76"
check "  STOPPED is not claimed" "$(grep -c 'STOPPED and the site is not serving' "$SANDBOX/out")" "0"
check "  the operator is told the state is not confirmed" "$(grep -c 'could NOT be confirmed stopped' "$SANDBOX/out")" "1"
check "  the journal says unknown" \
    "$(grep -c '^apps           unknown' "$SANDBOX/manifests/in-progress" 2>/dev/null)" "1"
rm -rf "$SANDBOX"

new_sandbox
# No previous deployment: nothing to restore, and the partial start is stopped.
touch "$STUB_DIR/app_recreate_partial"
run_guard
check "a partial start on a first deployment: refused" "$RC" "76"
check "  backend is stopped again" "$(cat "$STUB_DIR/live_backend" 2>/dev/null)" "exited"
check "  the journal says stopped" \
    "$(grep -c '^apps           stopped' "$SANDBOX/manifests/in-progress" 2>/dev/null)" "1"
rm -rf "$SANDBOX"

echo "== no dependency start names an application, even when the graph would =="
# backend depending on scheduler and on nginx: the transitive closure contains
# both, and starting them `--no-deps` by name is exactly the revival the
# forced recreate exists to prevent.
new_sandbox
cat > "$STUB_DIR/rendered" <<'JSON'
{"services": {
  "nginx":     {"ports": [{"target": 80,  "published": "80",  "protocol": "tcp", "mode": "ingress"},
                          {"target": 443, "published": "443", "protocol": "tcp", "mode": "ingress"}],
                "depends_on": {"backend": {"condition": "service_started"}}},
  "redis":     {},
  "backend":   {"environment": {"SORA_ENV": "production"},
                "depends_on": {"scheduler": {"condition": "service_started"}, "nginx": {"condition": "service_started"}, "redis": {"condition": "service_started"}}},
  "scheduler": {"environment": {"SORA_ENV": "production"},
                "depends_on": {"redis": {"condition": "service_started"}}}
}}
JSON
run_guard
check "backend depending on scheduler and nginx: the deployment succeeds" "$RC" "0"
check "  only redis is started on its own" "$(sed 's/.*--no-deps //' "$STUB_DIR/deps_started" 2>/dev/null)" "redis"
check "  and no up in the window names an application or nginx" "$(reviving_ups | tr '\n' '|')" ""
rm -rf "$SANDBOX"

echo "== the migration and the application are one image, not two builds =="
# `migrate`, `backend` and `scheduler` share one tag and only `backend` builds
# it, so "migrated from the image being deployed" is a fact rather than a
# resemblance. These cases are about the script honouring that.

new_sandbox
run_guard
check "the image is built once, explicitly" \
    "$(grep -c 'build backend' "$STUB_DIR/calls")" "1"
# --no-build on the start: a rebuild there could produce a different image than
# the one just migrated from, which is the gap the shared tag closes.
check "and the containers start without rebuilding" \
    "$(grep -c 'up -d --no-build --remove-orphans' "$STUB_DIR/calls")" "1"
BUILD_LINE="$(grep -n 'build backend' "$STUB_DIR/calls" | head -1 | cut -d: -f1)"
MIG_LINE="$(grep -nE 'run -d --no-deps .* migrate$' "$STUB_DIR/calls" | head -1 | cut -d: -f1)"
check "and the build comes before the migration" \
    "$([ -n "$BUILD_LINE" ] && [ -n "$MIG_LINE" ] && [ "$BUILD_LINE" -lt "$MIG_LINE" ] && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

new_sandbox
touch "$STUB_DIR/build_fails"
run_guard
refused_because "an image that will not build refuses the deployment" "would not build"
check "and no migration was attempted" \
    "$(cat "$STUB_DIR/migrate_calls" 2>/dev/null || echo 0)" "0"
rm -rf "$SANDBOX"

new_sandbox
# The tag resolves to one id; the running container reports another. Whatever
# the cause -- something rebuilt behind the script, the tag moved -- the
# migration belongs to a build nobody is serving.
echo "sha256:a-different-image" > "$STUB_DIR/app_image_id"
# And the targeted recreate does not move it either -- otherwise this case would
# now pass, since disagreeing with the build is exactly what triggers it.
touch "$STUB_DIR/recreate_does_not_fix"
run_guard
refused_because "a container running a different image than the migration" "not the sha256:a-different-image the migration ran from"
check "after one targeted recreate was tried" \
    "$(grep -c -- '--no-deps --force-recreate backend scheduler' "$STUB_DIR/calls")" "1"
rm -rf "$SANDBOX"

echo "== backend and scheduler left on the previous image by up =="
# Production, 2026-10-26: a release that changed only Grafana provisioning built
# a new image under the same tag, and `up --no-build` left backend and scheduler
# running the old one. The release was refused and rolled back.
new_sandbox
echo "sha256:previous-release" > "$STUB_DIR/stale_app_image"
run_guard
check "a stale application image is recreated and the deployment succeeds" "$RC" "0"
check "with exactly one targeted recreate of backend and scheduler" \
    "$(grep -c -- 'up -d --no-build --no-deps --force-recreate backend scheduler' "$STUB_DIR/calls")" "1"
check "and no other force-recreate than backend+scheduler, nginx and prometheus" \
    "$(grep -- '--force-recreate' "$STUB_DIR/calls" | grep -vc -e '--no-deps --force-recreate backend scheduler$' -e '--force-recreate nginx$' -e '--force-recreate --no-deps prometheus$')" "0"
_recreate_line="$(grep -n -- '--no-deps --force-recreate backend scheduler' "$STUB_DIR/calls" | head -1 | cut -d: -f1)"
_nginx_line="$(grep -n -- 'up -d --force-recreate nginx' "$STUB_DIR/calls" | head -1 | cut -d: -f1)"
check "and it happens before nginx is recreated" \
    "$([ -n "$_recreate_line" ] && [ -n "$_nginx_line" ] && [ "$_recreate_line" -lt "$_nginx_line" ] && echo before || echo "recreate=$_recreate_line nginx=$_nginx_line")" "before"
check "and nothing was rolled back" "$([ -f "$STUB_DIR/rolled_back" ] && echo yes || echo no)" "no"
rm -rf "$SANDBOX"

new_sandbox
run_guard
check "images already right: the deployment succeeds" "$RC" "0"
# Before the ownership cutover this was "without a targeted recreate": the
# recreate ran only when `up` had left the old image. Now the first start after
# the migration is always a forced recreate, because the alternative is `up`
# deciding whether to revive the stopped old containers.
check "and backend and scheduler are still force-recreated, exactly once" \
    "$(grep -c -- '--no-deps --force-recreate backend scheduler' "$STUB_DIR/calls")" "1"
rm -rf "$SANDBOX"

new_sandbox
echo "sha256:previous-release" > "$STUB_DIR/stale_app_image"
touch "$STUB_DIR/recreate_does_not_fix"
run_guard
refused_because "a recreate that leaves the old image is refused" "not the sha256:image the migration ran from"
check "and it is refused before nginx is recreated" \
    "$(grep -c -- 'up -d --force-recreate nginx' "$STUB_DIR/calls")" "0"
rm -rf "$SANDBOX"

new_sandbox
echo "sha256:previous-release" > "$STUB_DIR/stale_app_image"
touch "$STUB_DIR/app_recreate_fails"
run_guard
refused_because "a recreate that fails is refused" "could not be recreated on the built image"
rm -rf "$SANDBOX"

echo "== the rollback keeps what its commands said =="
new_sandbox
# A previous deployment on record, so the rollback attempts the exact-image
# restore rather than stopping at "nothing to restore".
with_previous_deployment
echo "sha256:previous-release" > "$STUB_DIR/stale_app_image"
touch "$STUB_DIR/recreate_does_not_fix"
touch "$STUB_DIR/no_build_fails"
run_guard
_log="$(grep -o 'rollback log: [^ ]*' "$SANDBOX/out" | head -1 | cut -d' ' -f3)"
check "the rollback names its log" "$([ -n "$_log" ] && echo named || echo missing)" "named"
check "beside the journal" "$(dirname "${_log:-/x}")" "$SANDBOX/manifests"
check "and the log records each command and its exit code" \
    "$(grep -c '^\[exit ' "${_log:-/dev/null}" 2>/dev/null)" "$(grep -c '^  \[exit ' "$SANDBOX/out")"
check "with what the failed command said, not only its status" \
    "$(grep -c 'stub-compose: exact-image restore refused' "${_log:-/dev/null}" 2>/dev/null)" "1"
check "including the failed exact-image restore" \
    "$(awk '/up -d --no-build --remove-orphans/ {want=1; next} want && /^\[exit / {print; exit}' "${_log:-/dev/null}" 2>/dev/null)" "[exit 1]"
rm -rf "$SANDBOX"


echo "== the application containers do not migrate =="
# A static read of the shipped entrypoint, because the property is "this file
# contains no DDL command" and running it would need a database.
ENTRYPOINT="$(cd "$(dirname "$0")/.." && pwd)/entrypoint.sh"
check "entrypoint.sh exists" \
    "$([ -f "$ENTRYPOINT" ] && echo yes || echo no)" "yes"
check "and does not run alembic upgrade" \
    "$(grep -cE '^[^#]*alembic[[:space:]]+upgrade' "$ENTRYPOINT")" "0"
check "and does verify the schema version instead" \
    "$(grep -cE '^[^#]*verify_schema_head' "$ENTRYPOINT")" "1"

echo "== a run that finished everything and died before recording it =="
# #160. The deploying run built, migrated, recreated and verified, then its SSH
# session was killed at a ten-minute timeout. Production was correct and
# unrecorded: journal present, no manifest. --finalize proves the end state and
# writes what is missing; it does not deploy anything again.

# The journal a deploy leaves behind at the moment of interruption.
write_journal() {
    local target="$1" mode="${2:-deploy}"
    mkdir -p "$SANDBOX/manifests"
    {
        echo "run            20260814T032941Z-1038993"
        echo "state          mutating"
        echo "started        2026-08-14T03:29:41Z"
        echo "updated        2026-08-14T03:29:41Z"
        echo "mode           $mode"
        echo "target         $target"
        echo "previous       $FIRST"
        echo "pre_containers cid-old-1 cid-old-2"
        echo "images"
        echo "  backend	sha256:old-backend"
        echo "  nginx	sha256:old-nginx"
    } > "$SANDBOX/manifests/in-progress"
}

new_sandbox
write_journal "$SECOND"
run_guard --finalize
check "it finalizes the interrupted run" "$RC" "0"
check "and writes exactly one manifest" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "1"
check "and marks it as a reconciliation, not a deployment" \
    "$(grep -qc '^finalized      yes' "$SANDBOX/manifests"/*.txt && echo yes || echo no)" "yes"
check "and removes the journal" \
    "$([ -f "$SANDBOX/manifests/in-progress" ] && echo present || echo gone)" "gone"
# The whole point: nothing was deployed a second time.
check "it did not build" "$(grep -c 'build backend' "$STUB_DIR/calls")" "0"
check "it did not migrate" "$(grep -cE 'run -d --no-deps .* migrate$' "$STUB_DIR/calls")" "0"
check "it did not recreate the services" \
    "$(grep -c 'up -d --no-build --remove-orphans' "$STUB_DIR/calls")" "0"
# The value, not the presence of the field. There is no earlier manifest in this
# sandbox, so a run reading the commit from the manifest chain -- as a deploy
# correctly does -- writes `none-recorded` here and still has the line.
check "and it recorded what the interrupted run replaced, not itself" \
    "$(awk '/^previous_commit /{print $2}' "$SANDBOX/manifests"/*.txt)" "$FIRST"
rm -rf "$SANDBOX"

new_sandbox
# The two sources of "what was running" disagree. The journal was written before
# the interrupted run touched anything; the manifest was published by whatever
# ran last. Nothing here can tell which describes what is serving, so the record
# is not written from either.
write_journal "$SECOND"
mkdir -p "$SANDBOX/manifests"
# A literal rather than a commit of this sandbox: the only property needed is
# "not the one the journal names", and borrowing a variable set inside an
# earlier case would tie this to the order those cases happen to run in.
printf 'commit %s\n' "0000000000000000000000000000000000000042" \
    > "$SANDBOX/manifests/prev.txt"
ln -sf prev.txt "$SANDBOX/manifests/latest"
run_guard --finalize
refused_because "a journal and a manifest that disagree about what was replaced" "they disagree about what was running"
check "and no manifest was written" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' ! -name 'prev.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
check "and the journal is left for a human" \
    "$([ -f "$SANDBOX/manifests/in-progress" ] && echo present || echo gone)" "present"
rm -rf "$SANDBOX"

new_sandbox
run_guard --finalize
refused_because "finalize with no journal refuses" "nothing to finalize"
check "and writes no manifest" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
rm -rf "$SANDBOX"

echo "== finalize refuses whatever it cannot prove =="

new_sandbox
# Interrupted before the migration: the checkout is still the old commit, so
# what is running is not what the journal targeted.
write_journal "$SECOND"
git -C "$REPO" checkout --quiet --detach "$FIRST"
run_guard --finalize
refused_because "a checkout that has moved" "cannot be reconciled from a tree that has moved"
check "and no manifest was written" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
check "and the journal is left for a human" \
    "$([ -f "$SANDBOX/manifests/in-progress" ] && echo present || echo gone)" "present"
rm -rf "$SANDBOX"

new_sandbox
# Interrupted after the migration and before the recreate: the containers are
# still on the previous image, so they do not match the tag.
write_journal "$SECOND"
echo "sha256:a-different-image" > "$STUB_DIR/app_image_id"
run_guard --finalize
refused_because "containers not running the deployed image" "not the sha256:a-different-image the migration ran from"
check "and no manifest was written" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
rm -rf "$SANDBOX"

new_sandbox
# Interrupted after the recreate and before verification: finalize runs the
# verification now, and a service that is missing is still a refusal.
write_journal "$SECOND"
echo "nginx" > "$STUB_DIR/services"
echo "backend" >> "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
run_guard --finalize
refused_because "a declared service that is not running" "is not running"
rm -rf "$SANDBOX"

new_sandbox
write_journal "$SECOND"
echo 500 > "$STUB_DIR/http_code"
run_guard --finalize
check "an unhealthy site refuses" "$([ "$RC" = 0 ] && echo no || echo yes)" "yes"
check "and writes no manifest" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
rm -rf "$SANDBOX"

new_sandbox
# A rollback that was interrupted is not something this path may finish: the
# checkout is detached and the record it would write is a different shape.
write_journal "$SECOND" rollback
run_guard --finalize
refused_because "an interrupted rollback is not finalized" "only a deploy can be finalized"
rm -rf "$SANDBOX"

echo "== the journal outlives the manifest, not the other way round =="
# It used to be cleared *before* the manifest was written, so an interruption
# between the two left neither: no record of what is deployed, and no sign that
# anything was unfinished.
new_sandbox
run_guard
check "a normal deploy still ends with a manifest and no journal" \
    "$([ -f "$SANDBOX/manifests/in-progress" ] && echo present || echo gone)" "gone"
MANIFEST_COUNT="$(find "$SANDBOX/manifests" -type f -name '*.txt' | wc -l | tr -d ' ')"
check "and exactly one manifest" "$MANIFEST_COUNT" "1"
# Ordering, read from the script. The behavioural half cannot observe it --
# both orders leave the same end state on a run that is not interrupted -- and
# the order is the whole fix.
CLEAR_LINE="$(grep -n '^journal_clear$' "$SCRIPT" | tail -1 | cut -d: -f1)"
LATEST_LINE="$(grep -n 'mv -Tf .*LATEST' "$SCRIPT" | head -1 | cut -d: -f1)"
if [ -n "$CLEAR_LINE" ] && [ -n "$LATEST_LINE" ] && [ "$CLEAR_LINE" -gt "$LATEST_LINE" ]; then
    ORDER=after
else
    ORDER=before
fi
check "and the journal is cleared after the manifest is published, not before" \
    "$ORDER" "after"
rm -rf "$SANDBOX"

echo "== finalize proves the deployment happened, not merely that images match =="
# A tag is a pointer and `docker inspect` describes what is running now. Neither
# says *when* it started, so containers predating the interrupted run -- on the
# same tag, because it had not moved yet -- satisfy an image comparison. The age
# is the discriminator.

new_sandbox
write_journal "$SECOND"
echo "2026-08-14T03:00:00.000000000Z" > "$STUB_DIR/container_created"
run_guard --finalize
refused_because "containers older than the interrupted run" "before the interrupted run started"
check "and no manifest was written" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
check "and the journal is left in place" \
    "$([ -f "$SANDBOX/manifests/in-progress" ] && echo present || echo gone)" "present"
rm -rf "$SANDBOX"

new_sandbox
write_journal "$SECOND"
# A journal with no run id was not written by this script; reconciling it would
# publish a manifest whose provenance reads `unknown`.
grep -v '^run ' "$SANDBOX/manifests/in-progress" > "$SANDBOX/j.tmp"
mv "$SANDBOX/j.tmp" "$SANDBOX/manifests/in-progress"
run_guard --finalize
refused_because "a journal with no run id" "no run id"
check "and no manifest was written" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
rm -rf "$SANDBOX"

new_sandbox
write_journal "$SECOND"
touch "$STUB_DIR/schema_behind"
run_guard --finalize
refused_because "a schema the running code does not accept" "cannot be confirmed"
rm -rf "$SANDBOX"

new_sandbox
# `rollback-failed` describes a run that refused and tried to undo itself. What
# is running then is whatever the rollback managed, which is the one state
# nobody may write a manifest for.
write_journal "$SECOND"
sed -i.bak 's/^state          mutating/state          rollback-failed/' "$SANDBOX/manifests/in-progress"
run_guard --finalize
refused_because "a journal that is not in state mutating" "not 'mutating'"
rm -rf "$SANDBOX"

new_sandbox
# The retry shape: finalize twice. The second must not write a second manifest.
write_journal "$SECOND"
run_guard --finalize
check "the first finalize succeeds" "$RC" "0"
FIRST_COUNT="$(find "$SANDBOX/manifests" -type f -name '*.txt' | wc -l | tr -d ' ')"
run_guard --finalize
check "the second says it is already done" "$RC" "0"
check "and says so rather than sounding like a fault" \
    "$(grep -qc 'already finalized' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and writes no second manifest" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' | wc -l | tr -d ' ')" "$FIRST_COUNT"
rm -rf "$SANDBOX"
echo "== the deployed code is asked how it behaves, not only whether it answers =="
# The class this covers is "CI and the image build different applications", and
# no test suite can see it: on 2026-08-14 a catch-all registered only where the
# SPA has been built made every absent path answer 404 in CI and 405 on
# production, on the same commit, with every health check green (#177).
#
# Each case below reproduces that shape and asserts the deployment stops --
# without rolling back, and without recording itself.

new_sandbox
run_guard
check "a correct deployment passes the behaviour probe" "$RC" "0"
check "and says so" \
    "$(grep -qc 'behaviour matches inside the container and through nginx' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and records itself" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' | wc -l | tr -d ' ')" "1"
rm -rf "$SANDBOX"

new_sandbox
# The #177 defect exactly: an absent path claims the verb is the problem.
printf '%s\n' '{"absent_path":"/api/v1/__deploy_probe_absent__","absent":405,"get_only_path":"/api/v1/ab/stats","get_only":405}' \
    > "$STUB_DIR/probe_json"
run_guard
refused_because "an absent path answering 405 stops the deployment" "expected 404"
check "and nothing was rolled back" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo rolled || echo untouched)" "untouched"
check "and no manifest was written" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
check "and the journal is left for a human" \
    "$([ -f "$SANDBOX/manifests/in-progress" ] && echo present || echo gone)" "present"
check "and both recoveries are named" \
    "$(grep -qc -- '--rollback' "$SANDBOX/out" && grep -qc -- '--finalize' "$SANDBOX/out" && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

new_sandbox
# The mirror defect: answering 404 everywhere denies that real endpoints exist.
printf '%s\n' '{"absent_path":"/api/v1/__deploy_probe_absent__","absent":404,"get_only_path":"/api/v1/ab/stats","get_only":404}' \
    > "$STUB_DIR/probe_json"
run_guard
refused_because "a real GET-only route answering 404 stops the deployment" "expected 405 for a GET-only route"
check "and nothing was rolled back" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo rolled || echo untouched)" "untouched"
rm -rf "$SANDBOX"

# There is no case for "a published POST route denies POST". The probe no
# longer sends one: measured against production, the first sorted candidate is
# /api/v1/ab/predict, which writes to prediction_log -- so the check would have
# manufactured a prediction on every deployment, into the table the platform's
# own analytics read. A verification step must not change what it verifies.

new_sandbox
# nginx and the container disagreeing is itself the finding: a layer between the
# caller and the application is answering, and no test sees that layer.
echo "405" > "$STUB_DIR/probe_ext_absent"
run_guard
refused_because "nginx and the container disagreeing stops the deployment" "disagree about the same request"
check "and nothing was rolled back" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo rolled || echo untouched)" "untouched"
rm -rf "$SANDBOX"

new_sandbox
# A route table that cannot be read is not a pass. The check must refuse rather
# than skip: "could not look" and "looked and found nothing wrong" are different.
printf '%s\n' '{"error":"URLError"}' > "$STUB_DIR/probe_json"
run_guard
refused_because "an unreadable route table stops the deployment" "would not serve its own route table"
rm -rf "$SANDBOX"

new_sandbox
# And the probe choosing nothing is the same: an application with no GET-only
# or POST route to ask about leaves this check reporting on nothing.
printf '%s\n' '{"absent_path":"/api/v1/__deploy_probe_absent__","absent":404,"get_only_path":null,"get_only":null}' \
    > "$STUB_DIR/probe_json"
run_guard
refused_because "no probeable routes stops the deployment" "cannot report on an application it could not read"
rm -rf "$SANDBOX"

echo "== prometheus is recreated, so it re-reads the scrape configuration =="
# #275. `infra/prometheus.yml` is a bind-mounted single file, so the container
# holds the inode it started with and a git pull does not reach it. Measured on
# production 2026-09-07: the scrape target added by #267 was on disk and absent
# from the running process, four days after it was deployed. Prometheus was
# started without `--web.enable-lifecycle`, so there is no `/-/reload` to call
# and recreating the container is the only lever the script has.
new_sandbox
run_guard
check "the deployment succeeds" "$RC" "0"
check "prometheus was recreated" \
    "$([ -f "$STUB_DIR/prometheus_recreated" ] && echo yes || echo no)" "yes"
check "and exactly once" \
    "$(grep -c -- '--force-recreate --no-deps prometheus' "$STUB_DIR/calls")" "1"
# Ordering carries the whole point. A recreate before the containers start is a
# recreate against whatever `infra/prometheus.yml` said before this run, which
# is the state being fixed -- and both orders leave the same file behind, so
# only the sequence tells them apart.
_UP_LINE="$(grep -n -- 'up -d --no-build --remove-orphans' "$STUB_DIR/calls" | head -1 | cut -d: -f1)"
_PROM_LINE="$(grep -n -- '--force-recreate --no-deps prometheus' "$STUB_DIR/calls" | head -1 | cut -d: -f1)"
check "after the containers start, not before" \
    "$([ -n "$_UP_LINE" ] && [ -n "$_PROM_LINE" ] && [ "$_PROM_LINE" -gt "$_UP_LINE" ] \
        && echo after || echo "not-after up=$_UP_LINE prom=$_PROM_LINE")" "after"
check "it says so" \
    "$(grep -c 'prometheus recreated, so it reads' "$SANDBOX/out")" "1"
# --no-deps, and asserted rather than assumed. prometheus declares
# `depends_on: backend`; without the flag a compose that chose to recreate the
# dependency would recreate the backend after nginx was already recreated,
# which is #129 -- nginx holding an address that no longer exists.
check "and does not drag its dependencies with it" \
    "$(grep -c -- '--no-deps prometheus' "$STUB_DIR/calls")" "1"
# The recreate is the arrangement; this is the evidence. A container can come
# back with the wrong mount and exit 0 all the same.
check "and confirms what the container is actually holding" \
    "$(grep -c 'prometheus is running the checked-in scrape configuration' "$SANDBOX/out")" "1"
# Negative control for the case below: nothing here should have warned.
check "with no warning about the configuration" \
    "$(grep -c 'different scrape configuration' "$SANDBOX/out")" "0"
rm -rf "$SANDBOX"

echo "== a prometheus that will not come back does not undo the deployment =="
# The reason the recreate is guarded rather than bare. Under `set -e` a bare
# command here aborts, the EXIT trap runs, and a deployment whose site is
# serving is rolled back because the thing that watches it did not restart.
# nginx serves the site; prometheus watches it, and the two do not deserve the
# same verdict.
new_sandbox
touch "$STUB_DIR/prometheus_recreate_fails"
run_guard
check "the deployment still succeeds" "$RC" "0"
check "nothing was rolled back" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo rolled-back || echo kept)" "kept"
check "and the failure is stated, not swallowed" \
    "$(grep -c 'WARNING: prometheus was not recreated' "$SANDBOX/out")" "1"
check "naming what stops being collected" \
    "$(grep -c 'are not being collected' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

echo "== a prometheus holding a different configuration is reported =="
# The case the comparison exists for, and the negative control that makes the
# passing line above mean something: an unconditional `echo` would print
# "running the checked-in scrape configuration" here too.
new_sandbox
{
    echo "global:"
    echo "  scrape_interval: 15s"
    echo "scrape_configs:"
    echo '  - job_name: "sora-app"'
} > "$STUB_DIR/prometheus_live_conf"
run_guard
check "the deployment is not refused over monitoring" "$RC" "0"
check "nothing was rolled back" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo rolled-back || echo kept)" "kept"
check "the mismatch is reported" \
    "$(grep -c 'holds a different scrape configuration' "$SANDBOX/out")" "1"
check "and it does not also claim the configuration matches" \
    "$(grep -c 'prometheus is running the checked-in scrape configuration' "$SANDBOX/out")" "0"
rm -rf "$SANDBOX"

echo "== a configuration that cannot be read is not reported as a match =="
# Distinct from the case above on purpose. `$(...)` of a failing command is the
# empty string, and comparing that against a non-empty file gives "different" --
# the right verdict for the wrong reason, and the wrong verdict the day the
# repository file is empty. The status is checked, not the output.
new_sandbox
touch "$STUB_DIR/prometheus_conf_unreadable"
run_guard
check "the deployment still succeeds" "$RC" "0"
check "the read failure is reported as itself" \
    "$(grep -c "could not read prometheus's configuration" "$SANDBOX/out")" "1"
check "not as a match" \
    "$(grep -c 'prometheus is running the checked-in scrape configuration' "$SANDBOX/out")" "0"
check "and not as a mismatch either" \
    "$(grep -c 'holds a different scrape configuration' "$SANDBOX/out")" "0"
rm -rf "$SANDBOX"

echo "== the deployment reports whether prometheus is actually scraping =="
# The other half of #275, and the half the issue named as its acceptance test:
# "the target is in the configuration" and "the target is being scraped" are
# different claims. Comparing the configuration answers the first one only, and
# the first one was true for the whole four days the second was false.
new_sandbox
run_guard
check "the deployment succeeds" "$RC" "0"
check "every declared job is reported as scraped" \
    "$(grep -c 'prometheus is scraping every declared job' "$SANDBOX/out")" "1"
# Both names, not just the first: the extraction is a loop and a loop that stops
# after one element produces the same sentence.
check "naming sora-app" \
    "$(grep -c 'every declared job.*sora-app' "$SANDBOX/out")" "1"
check "and sora-scheduler" \
    "$(grep -c 'every declared job.*sora-scheduler' "$SANDBOX/out")" "1"
check "with nothing reported as missing" \
    "$(grep -c 'is not scraping' "$SANDBOX/out")" "0"
rm -rf "$SANDBOX"

echo "== a declared target that is not being scraped is named =="
# The state production was in: the job is in the file, prometheus knows nothing
# about it, and every other signal is green.
new_sandbox
echo 'up{instance="backend:8000", job="sora-app"} => 1 @[1788785159.269]' \
    > "$STUB_DIR/prometheus_up_lines"
run_guard
check "the deployment is not refused over monitoring" "$RC" "0"
check "nothing was rolled back" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo rolled-back || echo kept)" "kept"
check "the missing job is named" \
    "$(grep -c 'is not scraping.*sora-scheduler' "$SANDBOX/out")" "1"
check "the job that is fine is not named" \
    "$(grep -c 'is not scraping.*sora-app' "$SANDBOX/out")" "0"
check "and it does not also claim every job is scraped" \
    "$(grep -c 'scraping every declared job' "$SANDBOX/out")" "0"
rm -rf "$SANDBOX"

echo "== a target that is down is not the same as a target that is up =="
# `up` reports 0 for a target prometheus knows about and could not scrape, so
# the series is present under its own job name. A check that matched the name
# and not the value would call this healthy -- which is the failure mode the
# whole issue is about, one layer down.
new_sandbox
{
    echo 'up{instance="backend:8000", job="sora-app"} => 1 @[1788785159.269]'
    echo 'up{instance="scheduler:9000", job="sora-scheduler"} => 0 @[1788785159.269]'
} > "$STUB_DIR/prometheus_up_lines"
run_guard
check "the deployment still succeeds" "$RC" "0"
check "the failing target is reported as not scraped" \
    "$(grep -c 'is not scraping.*sora-scheduler' "$SANDBOX/out")" "1"
check "and not as scraped" \
    "$(grep -c 'scraping every declared job' "$SANDBOX/out")" "0"
rm -rf "$SANDBOX"

echo "== prometheus refusing to answer is reported, not read as healthy =="
# Third state. An empty answer contains no job names, so the loop finds
# everything missing -- the right verdict, and worth pinning: an implementation
# that defaulted to "assume fine when the query fails" would be silent exactly
# when the collector is broken.
new_sandbox
touch "$STUB_DIR/promtool_fails"
run_guard
check "the deployment still succeeds" "$RC" "0"
check "nothing was rolled back" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo rolled-back || echo kept)" "kept"
check "both jobs are reported as not scraped" \
    "$(grep -c 'is not scraping.*sora-app.*sora-scheduler' "$SANDBOX/out")" "1"
check "and nothing claims they are" \
    "$(grep -c 'scraping every declared job' "$SANDBOX/out")" "0"
rm -rf "$SANDBOX"

echo "== a deployment that would not arm the production guards is refused =="
# GHSA-x724-6jh7-g3fr. `app/auth.py` refuses a development JWT key and refuses
# the built-in account passwords, and `app/secret_validation.py` checks every
# secret -- all three only when SORA_ENV == "production". The compose file said
# `ENV: production` for months: the right intent under a name nothing in `app/`
# reads, so all three were off on a host whose configuration looked correct.
new_sandbox
python3 - "$STUB_DIR/rendered" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["services"]["backend"]["environment"]["SORA_ENV"] = "development"
json.dump(d, open(p, "w"))
PY
run_guard
refused_because "the deployment is refused" "would not arm the production guards"
# Twice on purpose: once in the reason, once in the SORA_ENV_ACKNOWLEDGE line
# the operator has to copy. Asserted as "at least once" rather than exactly,
# so rewording the message does not fail a test about the value being shown.
check "and the observed value is named, so the acknowledgement cannot be prepared in advance" \
    "$([ "$(grep -c 'backend=development,scheduler=production' "$SANDBOX/out")" -ge 1 ] && echo named || echo absent)" "named"
# The refusal must land before anything is recreated: a deployment that is
# going to be declined must not first replace the containers it is declining.
check "nothing was started" \
    "$(grep -c -- 'up -d --no-build --remove-orphans' "$STUB_DIR/calls")" "0"
rm -rf "$SANDBOX"

echo "== the acknowledgement carries the observed value, not a fixed word =="
# Same shape as ROLLBACK_ACKNOWLEDGE: the operator has to read the output to
# produce it, so it cannot be pasted from a runbook ahead of time.
new_sandbox
python3 - "$STUB_DIR/rendered" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
d["services"]["backend"]["environment"]["SORA_ENV"] = "development"
json.dump(d, open(sys.argv[1], "w"))
PY
SORA_ENV_ACKNOWLEDGE="yes" run_guard
refused_because "a wrong acknowledgement does not unlock it" "would not arm the production guards"
rm -rf "$SANDBOX"

new_sandbox
python3 - "$STUB_DIR/rendered" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
d["services"]["backend"]["environment"]["SORA_ENV"] = "development"
json.dump(d, open(sys.argv[1], "w"))
PY
SORA_ENV_ACKNOWLEDGE="backend=development,scheduler=production" run_guard
check "the matching acknowledgement lets it through" "$RC" "0"
check "and it says so, loudly" \
    "$(grep -c 'WARNING: deploying without the production guards' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

echo "== the value the container actually received is read back =="
# The declaration is the arrangement; this is the evidence. A value can be
# declared and still not arrive -- an override file, a stale container, a typo
# in a name -- and this whole advisory is about a correct-looking declaration
# that produced no guard.
new_sandbox
run_guard
check "a normal deployment succeeds" "$RC" "0"
check "and confirms the guards are active" \
    "$(grep -c 'backend runs with SORA_ENV=production; the secret guards are active' "$SANDBOX/out")" "1"
check "with no warning" \
    "$(grep -c 'the secret guards are inert' "$SANDBOX/out")" "0"
rm -rf "$SANDBOX"

echo "== a container that did not receive it is reported, not silently accepted =="
# The negative control for the line above: an unconditional echo would print
# "guards are active" here too.
new_sandbox
echo "development" > "$STUB_DIR/live_sora_env"
run_guard
check "the deployment is not undone over it" "$RC" "0"
check "the mismatch is reported" \
    "$(grep -c 'backend reports SORA_ENV=development' "$SANDBOX/out")" "1"
check "and it does not also claim the guards are active" \
    "$(grep -c 'the secret guards are active' "$SANDBOX/out")" "0"
rm -rf "$SANDBOX"

echo "== database degraded: the deployment is refused and rolled back =="
# The defect (#21): _check_db returns "degraded" on error, not "unhealthy", so
# the overall status is still "healthy" with 200. The script checks HTTP 200 but
# must also check checks.database.status in the body.
# A previous deployment is recorded so the rollback actually happens and is observable.
# Without one, the script takes the exit-76 path (nothing to restore) instead of a rollback.
new_sandbox
with_previous_deployment
cat > "$STUB_DIR/health_body" <<'JSON'
{"status":"healthy","checks":{"database":{"status":"degraded","error":"connection refused"},"models":{"status":"healthy"},"external_data":{"status":"healthy"}}}
JSON
run_guard
refused_because "database degraded is refused" "database health check reports 'degraded'"
check "the refusal names what it expected" \
    "$(grep -c "expected 'healthy'" "$SANDBOX/out")" "1"
check "and it rolled back" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo yes || echo no)" "yes"
check "a degraded database: exit says the previous state is back" "$RC" "1"
check "a degraded database: the recorded images were restored" \
    "$(grep -qc 'restored the images that were running' "$SANDBOX/out" && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== database healthy: the deployment is accepted =="
# The control: when the database is healthy, the check passes.
new_sandbox
cat > "$STUB_DIR/health_body" <<'JSON'
{"status":"healthy","checks":{"database":{"status":"healthy"},"models":{"status":"healthy"},"external_data":{"status":"healthy"}}}
JSON
run_guard
check "database healthy is accepted" "$RC" "0"
check "and it confirms the database health" \
    "$(grep -c 'database health: healthy' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

echo "== a failing case fails the run, wherever it is written =="
# Not a case about deployment. The tally used to be two statements at the bottom
# of this file, and the exit status of a script is that of its last command --
# so a section appended below them counted its failures into $FAIL and left the
# status of whatever it ended with. One real FAIL was printed and the job went
# green.
#
# Asserted by running this file's own reporting in a subshell rather than by
# reading it, because "the trap is registered" and "the status is non-zero" are
# different claims and only the second one matters.
_TALLY_PROBE="$(mktemp)"
cat > "$_TALLY_PROBE" <<'PROBE'
PASS=0; FAIL=0
_report_tally() {
    local rc=$?
    echo "  passed: $PASS   failed: $FAIL"
    if [ "$FAIL" -ne 0 ]; then exit 1; fi
    exit "$rc"
}
trap _report_tally EXIT
FAIL=1
true            # a last command that succeeds, as `rm -rf` did
PROBE
bash "$_TALLY_PROBE" >/dev/null 2>&1
check "a run ending on a successful command still fails when a case failed" "$?" "1"
cat > "$_TALLY_PROBE" <<'PROBE'
PASS=0; FAIL=0
_report_tally() {
    local rc=$?
    if [ "$FAIL" -ne 0 ]; then exit 1; fi
    exit "$rc"
}
trap _report_tally EXIT
true
PROBE
bash "$_TALLY_PROBE" >/dev/null 2>&1
check "and a run with no failures still succeeds" "$?" "0"
rm -f "$_TALLY_PROBE"
