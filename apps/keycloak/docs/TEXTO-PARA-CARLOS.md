# Login único da intranet — o que vamos fazer (para o Carlos)

**Objetivo:** hoje Monitoramento e Frota têm logins separados. Vamos ter uma conta só (a do AD), e cada pessoa entra apenas nos apps em que está autorizada.

**Como:** instalamos o Keycloak (container no TrueNAS, portas 8180/8543). Ele vira o "porteiro": os apps enviam o usuário para ele e ele devolve quem é e a quais apps tem acesso. Nada do que está em produção é alterado; o botão "Entrar com a conta Safenet" é adicionado ao lado do login atual, e o antigo só sai no final.

**Fases:**
1. Piloto com usuários locais no Keycloak (sem depender do AD).
2. Federação com o AD (a sua parte).
3. Desligar os logins antigos.

**O que preciso de você (fase 2):**
- Conta de serviço somente leitura no AD (`svc-keycloak`), senha sem expiração.
- Grupos `GS_App_Monitoramento`, `GS_App_Frota` etc. com os membros corretos (quem está no grupo, tem acesso ao app).
- Confirmar controlador 192.168.0.50 e base `DC=ad,DC=safenet,DC=com,DC=br`.
- Certificado para LDAPS (636). **A instalação do AD CS só será feita com autorização e validação do Allan — não instale por conta própria.**

Detalhes técnicos: `FEDERACAO-AD.md`.
