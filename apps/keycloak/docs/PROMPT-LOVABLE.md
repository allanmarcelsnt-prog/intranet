# Prompt para o Lovable — botão "Entrar com a conta Safenet"

Usar no projeto de cada app (Monitoramento, Frota), em ambiente de teste. Não altera o login atual.

```
Na tela de login, mantenha o formulário atual de e-mail e senha e adicione abaixo dele
um botão secundário "Entrar com a conta Safenet", separado por um divisor "ou".
Ao clicar, chame:
  supabase.auth.signInWithOAuth({ provider: 'keycloak', options: { scopes: 'openid', redirectTo: window.location.origin } })
Ao voltar do login, trate a sessão normalmente (onAuthStateChange). Não remova nem altere
o login por senha, a tabela de aprovação de operadores (operator_access) nem as políticas RLS.
Se o usuário autenticar pelo Keycloak mas não estiver aprovado, mostre a mesma tela de
"aguardando aprovação" que já existe.
```

Lado servidor (GoTrue): ver `LEIAME.md`, seção "Integrar um app Supabase".
