-- Executado uma única vez, na criação do banco. A imagem do Supabase cria estes papéis sem senha.
\set pgpass `echo "$POSTGRES_PASSWORD"`
ALTER USER authenticator       WITH PASSWORD :'pgpass';
ALTER USER supabase_auth_admin WITH PASSWORD :'pgpass';
