# Federação com o Active Directory (fase 2)

Responsável pelo AD: Carlos. Só começa depois do piloto com usuários locais estar validado.

## O que o Carlos precisa fornecer

1. Uma conta de serviço **somente leitura** no AD (ex.: `svc-keycloak`), senha que não expira, sem login interativo.
2. Confirmar: controlador `192.168.0.50`, base `DC=ad,DC=safenet,DC=com,DC=br`.
3. Os grupos `GS_App_<Slug>` por app (ex.: `GS_App_Monitoramento`, `GS_App_Frota`) com os usuários certos.
4. **LDAPS (porta 636)** com certificado confiável — depende de AD CS / CA interna. **Instalação do AD CS está adiada e só ocorre com autorização e validação do Allan.** Até lá, usar usuários locais.

## Configuração no Keycloak (User federation → LDAP)

- Vendor: Active Directory · Connection URL `ldaps://192.168.0.50:636` · Edit mode **READ_ONLY**
- Username LDAP attribute `sAMAccountName` · RDN `cn` · UUID `objectGUID` · User object classes `person, organizationalPerson, user`
- Importar o certificado da CA no truststore do Keycloak (`/opt/keycloak/conf/truststores`).
- Mapper `group-ldap-mapper` apontando para a OU dos grupos `GS_`.
- Mapear cada grupo importado ao papel `acesso` do app correspondente (Role mapping do grupo), para o fluxo `acesso-<slug>` continuar valendo.

## Validar antes de ligar para todos

Teste com 1–2 usuários: login funciona, grupo importado, papel `acesso` aparece no token, e-mail preenchido. O comportamento do nome do grupo/mapeamento de papéis com grupos importados ainda **não foi validado** e precisa desse teste.
