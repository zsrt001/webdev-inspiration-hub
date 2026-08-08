-- Run once through the exact Production Supabase SQL Editor or Management API
-- before migration 20260710_0021. This addendum creates only the observation
-- roles and their password-rotation function. It never reads or rotates an
-- existing Production credential.

CREATE OR REPLACE FUNCTION pg_temp.bootstrap_vowpic_observation_database_roles()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $bootstrap$
DECLARE
    current_revision text;
    invalid_role_count integer;
    unexpected_memberships text[];
    function_owner text;
    function_is_security_definer boolean;
    function_config text[];
BEGIN
    IF current_database() <> 'postgres' THEN
        RAISE EXCEPTION
          'VowPic observation-role bootstrap must run in the postgres database';
    END IF;
    IF current_user <> 'postgres' AND NOT COALESCE(
        (SELECT rolsuper FROM pg_roles WHERE rolname = current_user),
        false
    ) THEN
        RAISE EXCEPTION
          'VowPic observation-role bootstrap requires the Supabase SQL Editor authority';
    END IF;
    IF NOT COALESCE(
        (SELECT rolcreaterole FROM pg_roles WHERE rolname = current_user),
        false
    ) AND NOT COALESCE(
        (SELECT rolsuper FROM pg_roles WHERE rolname = current_user),
        false
    ) THEN
        RAISE EXCEPTION
          'VowPic observation-role bootstrap authority lacks CREATEROLE';
    END IF;
    IF to_regclass('public.alembic_version') IS NULL THEN
        RAISE EXCEPTION 'VowPic Production alembic_version is missing';
    END IF;
    SELECT version_num INTO STRICT current_revision
    FROM public.alembic_version;
    IF current_revision NOT IN ('20260712_0014', '20260710_0020', '20260710_0021') THEN
        RAISE EXCEPTION
          'unsupported VowPic Production revision for observation roles: %',
          current_revision;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = 'vowpic_migration_owner'
          AND NOT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb
          AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls
          AND rolinherit
    ) THEN
        RAISE EXCEPTION 'vowpic_migration_owner violates the prerequisite contract';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = 'vowpic_migration_login'
          AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb
          AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls
          AND rolinherit
    ) OR NOT pg_has_role(
        'vowpic_migration_login',
        'vowpic_migration_owner',
        'MEMBER'
    ) THEN
        RAISE EXCEPTION 'vowpic_migration_login violates the prerequisite contract';
    END IF;

    IF current_setting('server_version_num')::integer >= 160000 THEN
        PERFORM set_config('createrole_self_grant', 'set', true);
    END IF;
    IF EXISTS (
        SELECT 1
        FROM (VALUES
            ('vowpic_runtime'),
            ('vowpic_control_writer'),
            ('vowpic_identity_service')
        ) AS required_role(rolname)
        LEFT JOIN pg_roles role ON role.rolname = required_role.rolname
        WHERE role.oid IS NULL
           OR role.rolcanlogin OR role.rolsuper OR role.rolcreatedb
           OR role.rolcreaterole OR role.rolreplication OR role.rolbypassrls
           OR NOT role.rolinherit
    ) THEN
        RAISE EXCEPTION
          'a required existing VowPic database group violates the prerequisite contract';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'vowpic_observation_reader'
    ) THEN
        CREATE ROLE vowpic_observation_reader
          NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
          NOREPLICATION NOBYPASSRLS INHERIT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'vowpic_observation_writer'
    ) THEN
        CREATE ROLE vowpic_observation_writer
          NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
          NOREPLICATION NOBYPASSRLS INHERIT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = 'vowpic_observation_reader_login'
    ) THEN
        CREATE ROLE vowpic_observation_reader_login
          NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
          NOREPLICATION NOBYPASSRLS INHERIT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = 'vowpic_observation_writer_login'
    ) THEN
        CREATE ROLE vowpic_observation_writer_login
          NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
          NOREPLICATION NOBYPASSRLS INHERIT;
    END IF;

    SELECT count(*) INTO invalid_role_count
    FROM pg_roles
    WHERE rolname IN (
        'vowpic_observation_reader',
        'vowpic_observation_writer'
    ) AND (
        rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR
        rolreplication OR rolbypassrls OR NOT rolinherit
    );
    IF invalid_role_count <> 0 THEN
        RAISE EXCEPTION
          'an observation database group violates the least-privilege contract';
    END IF;

    SELECT count(*) INTO invalid_role_count
    FROM pg_roles
    WHERE rolname IN (
        'vowpic_observation_reader_login',
        'vowpic_observation_writer_login'
    ) AND (
        rolsuper OR rolcreatedb OR rolcreaterole OR
        rolreplication OR rolbypassrls OR NOT rolinherit
    );
    IF invalid_role_count <> 0 THEN
        RAISE EXCEPTION
          'an observation database login violates the least-privilege contract';
    END IF;

    SELECT array_agg(member.rolname || ':' || parent.rolname
                     ORDER BY member.rolname, parent.rolname)
    INTO unexpected_memberships
    FROM pg_auth_members membership
    JOIN pg_roles member ON member.oid = membership.member
    JOIN pg_roles parent ON parent.oid = membership.roleid
    WHERE member.rolname IN (
        'vowpic_observation_reader',
        'vowpic_observation_writer'
    );
    IF coalesce(unexpected_memberships, ARRAY[]::text[]) <> ARRAY[]::text[] THEN
        RAISE EXCEPTION 'an observation database group has unexpected memberships';
    END IF;

    SELECT array_agg(parent.rolname ORDER BY parent.rolname)
    INTO unexpected_memberships
    FROM pg_auth_members membership
    JOIN pg_roles member ON member.oid = membership.member
    JOIN pg_roles parent ON parent.oid = membership.roleid
    WHERE member.rolname = 'vowpic_observation_reader_login';
    IF coalesce(unexpected_memberships, ARRAY[]::text[]) <> ARRAY[]::text[]
       AND coalesce(unexpected_memberships, ARRAY[]::text[]) <>
           ARRAY['vowpic_observation_reader']::text[] THEN
        RAISE EXCEPTION
          'observation-reader login has unexpected memberships';
    END IF;

    SELECT array_agg(parent.rolname ORDER BY parent.rolname)
    INTO unexpected_memberships
    FROM pg_auth_members membership
    JOIN pg_roles member ON member.oid = membership.member
    JOIN pg_roles parent ON parent.oid = membership.roleid
    WHERE member.rolname = 'vowpic_observation_writer_login';
    IF coalesce(unexpected_memberships, ARRAY[]::text[]) <> ARRAY[]::text[]
       AND coalesce(unexpected_memberships, ARRAY[]::text[]) <>
           ARRAY['vowpic_observation_writer']::text[] THEN
        RAISE EXCEPTION
          'observation-writer login has unexpected memberships';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_roles role
        WHERE role.rolname IN (
            'vowpic_observation_reader',
            'vowpic_observation_writer',
            'vowpic_observation_reader_login',
            'vowpic_observation_writer_login'
        ) AND (
            EXISTS (
                SELECT 1 FROM pg_database database
                WHERE database.datdba = role.oid
            ) OR EXISTS (
                SELECT 1 FROM pg_namespace namespace
                WHERE namespace.nspowner = role.oid
            ) OR EXISTS (
                SELECT 1 FROM pg_class relation
                WHERE relation.relowner = role.oid
            ) OR EXISTS (
                SELECT 1 FROM pg_proc routine
                WHERE routine.proowner = role.oid
            )
        )
    ) THEN
        RAISE EXCEPTION 'an observation database role owns database objects';
    END IF;

    EXECUTE $ddl$
        CREATE OR REPLACE FUNCTION public.vowpic_rotate_observation_database_logins(
            reader_password text,
            writer_password text
        )
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $rotate_observation$
        BEGIN
            IF session_user <> 'vowpic_migration_login'
               OR NOT pg_has_role(
                   session_user,
                   'vowpic_migration_owner',
                   'MEMBER'
               ) THEN
                RAISE EXCEPTION
                  'observation database login rotation requires the migration login';
            END IF;
            IF length(reader_password) < 64 OR length(writer_password) < 64
               OR reader_password = writer_password THEN
                RAISE EXCEPTION
                  'observation database login passwords are invalid';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_roles
                WHERE rolname IN (
                    'vowpic_observation_reader_login',
                    'vowpic_observation_writer_login'
                ) AND (
                    rolsuper OR rolcreatedb OR rolcreaterole OR
                    rolreplication OR rolbypassrls OR NOT rolinherit
                )
            ) THEN
                RAISE EXCEPTION
                  'observation database login violates the least-privilege contract';
            END IF;
            EXECUTE format(
                'ALTER ROLE vowpic_observation_reader_login '
                'WITH LOGIN PASSWORD %L VALID UNTIL ''infinity''',
                reader_password
            );
            EXECUTE format(
                'ALTER ROLE vowpic_observation_writer_login '
                'WITH LOGIN PASSWORD %L VALID UNTIL ''infinity''',
                writer_password
            );
            REVOKE vowpic_runtime FROM vowpic_observation_reader_login;
            REVOKE vowpic_control_writer FROM vowpic_observation_reader_login;
            REVOKE vowpic_identity_service FROM vowpic_observation_reader_login;
            REVOKE vowpic_observation_writer FROM vowpic_observation_reader_login;
            REVOKE vowpic_runtime FROM vowpic_observation_writer_login;
            REVOKE vowpic_control_writer FROM vowpic_observation_writer_login;
            REVOKE vowpic_identity_service FROM vowpic_observation_writer_login;
            REVOKE vowpic_observation_reader FROM vowpic_observation_writer_login;
            GRANT vowpic_observation_reader
              TO vowpic_observation_reader_login;
            GRANT vowpic_observation_writer
              TO vowpic_observation_writer_login;
            ALTER ROLE vowpic_observation_reader_login
              SET default_transaction_read_only = on;
            ALTER ROLE vowpic_observation_reader_login
              SET statement_timeout = '30s';
            ALTER ROLE vowpic_observation_writer_login
              RESET default_transaction_read_only;
            ALTER ROLE vowpic_observation_writer_login
              SET statement_timeout = '30s';
        END
        $rotate_observation$
    $ddl$;
    ALTER FUNCTION public.vowpic_rotate_observation_database_logins(text, text)
      OWNER TO postgres;
    REVOKE ALL ON FUNCTION
      public.vowpic_rotate_observation_database_logins(text, text)
      FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION
      public.vowpic_rotate_observation_database_logins(text, text)
      TO vowpic_migration_owner;

    SELECT owner.rolname, procedure.prosecdef, procedure.proconfig
    INTO function_owner, function_is_security_definer, function_config
    FROM pg_proc procedure
    JOIN pg_roles owner ON owner.oid = procedure.proowner
    WHERE procedure.oid = to_regprocedure(
        'public.vowpic_rotate_observation_database_logins(text,text)'
    );
    IF function_owner <> 'postgres'
       OR NOT function_is_security_definer
       OR function_config <> ARRAY['search_path=pg_catalog']::text[] THEN
        RAISE EXCEPTION
          'observation database login rotation function violates its owner contract';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_proc procedure,
             LATERAL aclexplode(
                 coalesce(
                     procedure.proacl,
                     acldefault('f', procedure.proowner)
                 )
             ) privilege
        WHERE procedure.oid = to_regprocedure(
            'public.vowpic_rotate_observation_database_logins(text,text)'
        )
          AND privilege.grantee = 0
          AND privilege.privilege_type = 'EXECUTE'
    ) OR NOT has_function_privilege(
        'vowpic_migration_owner',
        'public.vowpic_rotate_observation_database_logins(text,text)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION
          'observation database login rotation function violates its ACL contract';
    END IF;

    RETURN jsonb_build_object(
        'schema', 'vowpic.observation-role-bootstrap.v1',
        'state', 'READY',
        'database', current_database(),
        'schema_revision', current_revision,
        'roles', (
            SELECT jsonb_agg(
                jsonb_build_object(
                    'name', role.rolname,
                    'can_login', role.rolcanlogin,
                    'superuser', role.rolsuper,
                    'create_db', role.rolcreatedb,
                    'create_role', role.rolcreaterole,
                    'replication', role.rolreplication,
                    'bypass_rls', role.rolbypassrls,
                    'memberships', coalesce((
                        SELECT jsonb_agg(parent.rolname ORDER BY parent.rolname)
                        FROM pg_auth_members membership
                        JOIN pg_roles parent ON parent.oid = membership.roleid
                        WHERE membership.member = role.oid
                    ), '[]'::jsonb)
                ) ORDER BY role.rolname
            )
            FROM pg_roles role
            WHERE role.rolname IN (
                'vowpic_observation_reader',
                'vowpic_observation_writer',
                'vowpic_observation_reader_login',
                'vowpic_observation_writer_login'
            )
        ),
        'rotation_function_owner', function_owner,
        'rotation_function_public_execute', false,
        'rotation_function_migration_execute', true
    );
END
$bootstrap$;

SELECT pg_temp.bootstrap_vowpic_observation_database_roles() AS result;
