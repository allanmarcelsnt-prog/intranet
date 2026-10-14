-- As funções auxiliares das políticas RLS só precisam ser executadas por usuários autenticados.
REVOKE EXECUTE ON FUNCTION public.has_role(uuid, public.app_role) FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.can_manage(uuid)                 FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.is_approved(uuid)                FROM PUBLIC, anon;
GRANT  EXECUTE ON FUNCTION public.has_role(uuid, public.app_role)  TO authenticated, service_role;
GRANT  EXECUTE ON FUNCTION public.can_manage(uuid)                 TO authenticated, service_role;
GRANT  EXECUTE ON FUNCTION public.is_approved(uuid)                TO authenticated, service_role;
