-- Run once in the Supabase SQL Editor for the exact Production `postgres`
-- database. The only result is a short-lived JSON secret payload; copy it
-- directly into the protected-secret handoff and do not save or log it.

CREATE OR REPLACE FUNCTION pg_temp.bootstrap_vowpic_production_database_roles()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $bootstrap$
DECLARE
    inventory_password text :=
        replace(gen_random_uuid()::text, '-', '') ||
        replace(gen_random_uuid()::text, '-', '') ||
        replace(gen_random_uuid()::text, '-', '') ||
        replace(gen_random_uuid()::text, '-', '');
    migration_password text :=
        replace(gen_random_uuid()::text, '-', '') ||
        replace(gen_random_uuid()::text, '-', '') ||
        replace(gen_random_uuid()::text, '-', '') ||
        replace(gen_random_uuid()::text, '-', '');
    current_revision text;
    unexpected_memberships text[];
    relation record;
    routine record;
    invalid_role_count integer;
    writable_table_count integer;
BEGIN
    IF current_database() <> 'postgres' THEN
        RAISE EXCEPTION 'VowPic Production bootstrap must run in the postgres database';
    END IF;
    IF current_user <> 'postgres' AND NOT (
        SELECT rolsuper FROM pg_roles WHERE rolname = current_user
    ) THEN
        RAISE EXCEPTION 'VowPic Production bootstrap requires the Supabase SQL Editor authority';
    END IF;
    IF to_regclass('public.alembic_version') IS NULL THEN
        RAISE EXCEPTION 'VowPic Production alembic_version is missing';
    END IF;
    SELECT version_num INTO STRICT current_revision FROM public.alembic_version;
    IF current_revision NOT IN ('20260427_0006', '20260516_0012', '20260712_0014') THEN
        RAISE EXCEPTION 'unsupported VowPic Production revision: %', current_revision;
    END IF;

    -- PostgreSQL 17 does not give a non-superuser CREATEROLE creator SET on a
    -- newly created role by default. Request that membership option at role
    -- creation time; hosted Supabase terminates the management connection when
    -- its protected SQL Editor role is targeted by an explicit GRANT.
    PERFORM set_config('createrole_self_grant', 'set', true);
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vowpic_migration_owner') THEN
        CREATE ROLE vowpic_migration_owner
          NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT;
    END IF;
    IF NOT pg_has_role(CURRENT_USER, 'vowpic_migration_owner', 'SET') THEN
        RAISE EXCEPTION
          'the Supabase SQL Editor authority cannot SET ROLE vowpic_migration_owner';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vowpic_runtime') THEN
        CREATE ROLE vowpic_runtime
          NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vowpic_control_writer') THEN
        CREATE ROLE vowpic_control_writer
          NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vowpic_identity_owner') THEN
        CREATE ROLE vowpic_identity_owner
          NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vowpic_identity_service') THEN
        CREATE ROLE vowpic_identity_service
          NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT;
    END IF;
    GRANT vowpic_identity_owner TO vowpic_migration_owner
      WITH INHERIT FALSE, SET TRUE;

    SELECT count(*) INTO invalid_role_count
    FROM pg_roles
    WHERE rolname IN (
        'vowpic_migration_owner',
        'vowpic_runtime',
        'vowpic_control_writer',
        'vowpic_identity_owner',
        'vowpic_identity_service'
    )
      AND (
        rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR
        rolreplication OR rolbypassrls OR NOT rolinherit
      );
    IF invalid_role_count <> 0 THEN
        RAISE EXCEPTION 'an existing VowPic NOLOGIN role violates the least-privilege contract';
    END IF;

    SELECT array_agg(member.rolname || ':' || parent.rolname ORDER BY member.rolname, parent.rolname)
    INTO unexpected_memberships
    FROM pg_auth_members membership
    JOIN pg_roles member ON member.oid = membership.member
    JOIN pg_roles parent ON parent.oid = membership.roleid
    WHERE member.rolname IN (
        'vowpic_migration_owner',
        'vowpic_runtime',
        'vowpic_control_writer',
        'vowpic_identity_owner',
        'vowpic_identity_service'
    )
      AND NOT (
        member.rolname = 'vowpic_migration_owner'
        AND parent.rolname = 'vowpic_identity_owner'
      );
    IF coalesce(unexpected_memberships, ARRAY[]::text[]) <> ARRAY[]::text[] THEN
        RAISE EXCEPTION 'an existing VowPic NOLOGIN role has unexpected memberships';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_roles role
        WHERE role.rolname IN ('vowpic_runtime', 'vowpic_control_writer')
          AND (
              EXISTS (SELECT 1 FROM pg_database database WHERE database.datdba = role.oid) OR
              EXISTS (SELECT 1 FROM pg_namespace namespace WHERE namespace.nspowner = role.oid) OR
              EXISTS (SELECT 1 FROM pg_class owned_relation WHERE owned_relation.relowner = role.oid) OR
              EXISTS (SELECT 1 FROM pg_proc owned_routine WHERE owned_routine.proowner = role.oid)
          )
    ) THEN
        RAISE EXCEPTION 'a VowPic application group owns database objects';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vowpic_inventory_login') THEN
        CREATE ROLE vowpic_inventory_login
          LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vowpic_migration_login') THEN
        CREATE ROLE vowpic_migration_login
          LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vowpic_app_runtime') THEN
        CREATE ROLE vowpic_app_runtime
          NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vowpic_control_writer_login') THEN
        CREATE ROLE vowpic_control_writer_login
          NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT;
    END IF;

    SELECT count(*) INTO invalid_role_count
    FROM pg_roles
    WHERE (rolname IN ('vowpic_inventory_login', 'vowpic_migration_login') AND (
              NOT rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR
              rolreplication OR rolbypassrls OR NOT rolinherit
          ))
       OR (rolname IN ('vowpic_app_runtime', 'vowpic_control_writer_login') AND (
              rolsuper OR rolcreatedb OR rolcreaterole OR
              rolreplication OR rolbypassrls OR NOT rolinherit
          ));
    IF invalid_role_count <> 0 THEN
        RAISE EXCEPTION 'an existing VowPic login violates the least-privilege contract';
    END IF;

    SELECT array_agg(parent.rolname ORDER BY parent.rolname)
    INTO unexpected_memberships
    FROM pg_auth_members membership
    JOIN pg_roles member ON member.oid = membership.member
    JOIN pg_roles parent ON parent.oid = membership.roleid
    WHERE member.rolname = 'vowpic_inventory_login';
    IF coalesce(unexpected_memberships, ARRAY[]::text[]) <> ARRAY[]::text[] THEN
        RAISE EXCEPTION 'existing inventory login has unexpected role memberships';
    END IF;

    SELECT array_agg(parent.rolname ORDER BY parent.rolname)
    INTO unexpected_memberships
    FROM pg_auth_members membership
    JOIN pg_roles member ON member.oid = membership.member
    JOIN pg_roles parent ON parent.oid = membership.roleid
    WHERE member.rolname = 'vowpic_migration_login'
      AND parent.rolname <> 'vowpic_migration_owner';
    IF coalesce(unexpected_memberships, ARRAY[]::text[]) <> ARRAY[]::text[] THEN
        RAISE EXCEPTION 'existing migration login has unexpected role memberships';
    END IF;

    SELECT array_agg(parent.rolname ORDER BY parent.rolname)
    INTO unexpected_memberships
    FROM pg_auth_members membership
    JOIN pg_roles member ON member.oid = membership.member
    JOIN pg_roles parent ON parent.oid = membership.roleid
    WHERE member.rolname = 'vowpic_app_runtime'
      AND parent.rolname NOT IN ('vowpic_runtime', 'vowpic_identity_service');
    IF coalesce(unexpected_memberships, ARRAY[]::text[]) <> ARRAY[]::text[] THEN
        RAISE EXCEPTION 'existing runtime login has unexpected role memberships';
    END IF;

    SELECT array_agg(parent.rolname ORDER BY parent.rolname)
    INTO unexpected_memberships
    FROM pg_auth_members membership
    JOIN pg_roles member ON member.oid = membership.member
    JOIN pg_roles parent ON parent.oid = membership.roleid
    WHERE member.rolname = 'vowpic_control_writer_login'
      AND parent.rolname <> 'vowpic_control_writer';
    IF coalesce(unexpected_memberships, ARRAY[]::text[]) <> ARRAY[]::text[] THEN
        RAISE EXCEPTION 'existing control-writer login has unexpected role memberships';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_roles role
        WHERE role.rolname IN ('vowpic_app_runtime', 'vowpic_control_writer_login')
          AND (
              EXISTS (SELECT 1 FROM pg_database database WHERE database.datdba = role.oid) OR
              EXISTS (SELECT 1 FROM pg_namespace namespace WHERE namespace.nspowner = role.oid) OR
              EXISTS (
                  SELECT 1
                  FROM pg_class owned_relation
                  WHERE owned_relation.relowner = role.oid
              ) OR
              EXISTS (
                  SELECT 1
                  FROM pg_proc owned_routine
                  WHERE owned_routine.proowner = role.oid
              )
          )
    ) THEN
        RAISE EXCEPTION 'an application login owns database objects';
    END IF;

    EXECUTE format(
        'ALTER ROLE vowpic_inventory_login WITH LOGIN PASSWORD %L VALID UNTIL ''infinity''',
        inventory_password
    );
    ALTER ROLE vowpic_inventory_login SET default_transaction_read_only = on;
    ALTER ROLE vowpic_inventory_login SET statement_timeout = '5min';
    REVOKE ALL ON SCHEMA public FROM vowpic_inventory_login;
    GRANT USAGE ON SCHEMA public TO vowpic_inventory_login;
    REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM vowpic_inventory_login;
    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM vowpic_inventory_login;
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO vowpic_inventory_login;
    GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO vowpic_inventory_login;

    EXECUTE format(
        'ALTER ROLE vowpic_migration_login WITH LOGIN PASSWORD %L VALID UNTIL ''infinity''',
        migration_password
    );
    GRANT vowpic_migration_owner TO vowpic_migration_login;
    ALTER ROLE vowpic_migration_login IN DATABASE postgres
      SET role TO 'vowpic_migration_owner';
    ALTER ROLE vowpic_migration_login SET lock_timeout = '15s';
    ALTER ROLE vowpic_migration_login SET statement_timeout = '5min';
    REVOKE ALL ON SCHEMA public FROM vowpic_migration_login;
    GRANT USAGE, CREATE ON SCHEMA public TO vowpic_migration_owner;

    FOR relation IN
        SELECT namespace.nspname, class.relname, class.relkind
        FROM pg_class class
        JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
        WHERE namespace.nspname = 'public'
          AND class.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
          AND class.relowner <> 'vowpic_migration_owner'::regrole
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend dependency
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = class.oid
                AND dependency.deptype = 'e'
          )
          AND NOT (
            current_revision = '20260712_0014'
            AND class.relkind = 'S'
            AND class.relname = 'identity_legacy_fallback_uses_seq'
          )
    LOOP
        EXECUTE format(
            'ALTER %s %I.%I OWNER TO vowpic_migration_owner',
            CASE relation.relkind
                WHEN 'S' THEN 'SEQUENCE'
                WHEN 'v' THEN 'VIEW'
                WHEN 'm' THEN 'MATERIALIZED VIEW'
                WHEN 'f' THEN 'FOREIGN TABLE'
                ELSE 'TABLE'
            END,
            relation.nspname,
            relation.relname
        );
    END LOOP;

    FOR routine IN
        SELECT namespace.nspname, procedure.proname,
               pg_get_function_identity_arguments(procedure.oid) AS identity_arguments
        FROM pg_proc procedure
        JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
        WHERE namespace.nspname = 'public'
          AND procedure.proowner <> 'vowpic_migration_owner'::regrole
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend dependency
              WHERE dependency.classid = 'pg_proc'::regclass
                AND dependency.objid = procedure.oid
                AND dependency.deptype = 'e'
          )
          AND NOT (
            procedure.proname = 'vowpic_runtime_statement_audit'
            AND pg_get_function_identity_arguments(procedure.oid) = ''
          )
          AND NOT (
            procedure.proname = 'vowpic_rotate_application_database_logins'
            AND pg_get_function_identity_arguments(procedure.oid) =
                'runtime_password text, writer_password text'
          )
          AND NOT (
            current_revision = '20260712_0014'
            AND procedure.proname = 'app_current_user_id'
            AND pg_get_function_identity_arguments(procedure.oid) = ''
          )
    LOOP
        EXECUTE format(
            'ALTER ROUTINE %I.%I(%s) OWNER TO vowpic_migration_owner',
            routine.nspname,
            routine.proname,
            routine.identity_arguments
        );
    END LOOP;

    SET LOCAL ROLE vowpic_migration_owner;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
      GRANT SELECT ON TABLES TO vowpic_inventory_login;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
      GRANT SELECT ON SEQUENCES TO vowpic_inventory_login;
    RESET ROLE;

    IF NOT EXISTS (
        SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'
    ) THEN
        RAISE EXCEPTION 'Supabase pg_stat_statements is required for runtime DDL evidence';
    END IF;
    CREATE OR REPLACE FUNCTION public.vowpic_runtime_statement_audit()
    RETURNS TABLE(statement_count bigint, ddl_statement_count bigint)
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog
    AS $audit$
    DECLARE
        statistics_schema name;
    BEGIN
        SELECT namespace.nspname
        INTO STRICT statistics_schema
        FROM pg_extension extension
        JOIN pg_namespace namespace ON namespace.oid = extension.extnamespace
        WHERE extension.extname = 'pg_stat_statements';
        RETURN QUERY EXECUTE format(
            'SELECT coalesce(sum(statements.calls), 0)::bigint, '
            'coalesce(sum(statements.calls) FILTER (WHERE statements.query ~* '
            '''[[:<:]](create|alter|drop|truncate|grant|revoke|comment|vacuum|reindex|cluster)[[:>:]]''), 0)::bigint '
            'FROM %I.pg_stat_statements statements '
            'WHERE statements.userid = (SELECT oid FROM pg_roles WHERE rolname = ''vowpic_app_runtime'') '
            'AND statements.dbid = (SELECT oid FROM pg_database WHERE datname = current_database())',
            statistics_schema
        );
    END
    $audit$;
    ALTER FUNCTION public.vowpic_runtime_statement_audit() OWNER TO postgres;
    REVOKE ALL ON FUNCTION public.vowpic_runtime_statement_audit() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.vowpic_runtime_statement_audit()
      TO vowpic_migration_owner;

    CREATE OR REPLACE FUNCTION public.vowpic_rotate_application_database_logins(
        runtime_password text,
        writer_password text
    )
    RETURNS void
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog
    AS $rotate$
    BEGIN
        IF session_user <> 'vowpic_migration_login'
           OR NOT pg_has_role(session_user, 'vowpic_migration_owner', 'MEMBER') THEN
            RAISE EXCEPTION 'application database login rotation requires the migration login';
        END IF;
        IF length(runtime_password) < 64 OR length(writer_password) < 64
           OR runtime_password = writer_password THEN
            RAISE EXCEPTION 'application database login passwords are invalid';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname IN ('vowpic_app_runtime', 'vowpic_control_writer_login')
              AND (
                  rolsuper OR rolcreatedb OR rolcreaterole OR
                  rolreplication OR rolbypassrls OR NOT rolinherit
              )
        ) THEN
            RAISE EXCEPTION 'an application database login violates the least-privilege contract';
        END IF;
        EXECUTE format(
            'ALTER ROLE vowpic_app_runtime WITH LOGIN PASSWORD %L VALID UNTIL ''infinity''',
            runtime_password
        );
        EXECUTE format(
            'ALTER ROLE vowpic_control_writer_login WITH LOGIN PASSWORD %L VALID UNTIL ''infinity''',
            writer_password
        );
        REVOKE vowpic_control_writer FROM vowpic_app_runtime;
        REVOKE vowpic_runtime FROM vowpic_control_writer_login;
        REVOKE vowpic_identity_service FROM vowpic_control_writer_login;
        GRANT vowpic_runtime TO vowpic_app_runtime;
        GRANT vowpic_identity_service TO vowpic_app_runtime;
        GRANT vowpic_control_writer TO vowpic_control_writer_login;
    END
    $rotate$;
    ALTER FUNCTION public.vowpic_rotate_application_database_logins(text, text)
      OWNER TO postgres;
    REVOKE ALL ON FUNCTION public.vowpic_rotate_application_database_logins(text, text)
      FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.vowpic_rotate_application_database_logins(text, text)
      TO vowpic_migration_owner;

    SELECT count(*) INTO writable_table_count
    FROM information_schema.tables
    WHERE table_schema = 'public'
      AND (
        has_table_privilege('vowpic_inventory_login', format('%I.%I', table_schema, table_name), 'INSERT') OR
        has_table_privilege('vowpic_inventory_login', format('%I.%I', table_schema, table_name), 'UPDATE') OR
        has_table_privilege('vowpic_inventory_login', format('%I.%I', table_schema, table_name), 'DELETE') OR
        has_table_privilege('vowpic_inventory_login', format('%I.%I', table_schema, table_name), 'TRUNCATE')
      );
    IF writable_table_count <> 0 THEN
        RAISE EXCEPTION 'inventory login has a writable public table';
    END IF;

    RETURN jsonb_build_object(
        'schema_version', 'vowpic.database-bootstrap.secrets.v1',
        'database', current_database(),
        'schema_revision', current_revision,
        'inventory_login', 'vowpic_inventory_login',
        'inventory_password', inventory_password,
        'migration_login', 'vowpic_migration_login',
        'migration_password', migration_password
    );
END
$bootstrap$;

SELECT pg_temp.bootstrap_vowpic_production_database_roles() AS secret_payload;
