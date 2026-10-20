# Portal da intranet com login único (Keycloak)

`navegador → oauth2-proxy (login Keycloak) → backend da intranet`

- Todos precisam entrar com a conta Safenet (grupo `GS_App_Intranet`). Exceção: páginas públicas de candidatos e autoavaliação (lista no `docker-compose.yml` e em `gateway_auth.py`).
- A página inicial mostra só os apps que a pessoa pode usar (grupos `GS_App_*` do token).
- Frota e Monitoramento (telas atuais) passam a exigir `GS_App_Frota` / `GS_App_Monitoramento`: o backend valida o token do Keycloak (assinatura, emissor, validade, cliente) antes de responder.
- RH, Licitações etc. continuam usando o usuário local **ligado pelo e-mail**: o e-mail da conta Safenet precisa ser o mesmo cadastrado no usuário da intranet, senão a pessoa recebe "conta não ligada" (sem segundo login).
- "Sair" (rodapé) encerra a sessão no portal **e no Keycloak**.
- O backend só liga esse modo com `KEYCLOAK_ISSUER` definido. Sem ele, nada muda.

## Teste (cópia isolada, dados vazios)

```bash
cp -a /caminho/portal-teste /mnt/Safenet_Storage/apps/portal-teste && cd /mnt/Safenet_Storage/apps/portal-teste
./setup.sh http://192.168.0.171:8082 8082 http://192.168.0.171:8190
nano .env      # cole INTRANET_CLIENT_SECRET
docker compose -f docker-compose.yml -f docker-compose.teste.yml up -d --build
```

No Keycloak (antes): `kc.py app intranet --redirect http://192.168.0.171:8082/oauth2/callback --mostrar-secret` e `kc.py conceder <login> intranet` para cada pessoa da empresa.

## Produção (depois do teste, com autorização)

Mesmo `docker-compose.yml`, com `UPSTREAM` apontando para o backend atual e `PORTAL_URL` definitivo (idealmente HTTPS). Para o login único valer de fato, o backend atual passa a receber só tráfego do portal e `KEYCLOAK_ISSUER` entra no ambiente dele. Nada disso é feito sem decisão do Allan.
