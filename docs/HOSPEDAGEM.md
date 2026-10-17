# Hospedagem e operação

## Estrutura

O mesmo projeto pode ser executado em uma VPS Linux com Docker. O banco PostgreSQL e a API ficam na rede interna do Compose, sem portas publicadas. O serviço web fica por padrão em `127.0.0.1:8080`, disponível para um proxy reverso no host. Domínio, contratação da VPS e publicação não são executados automaticamente.

Para a primeira implantação:

1. Prepare um servidor com Docker, espaço para banco/comprovantes e um domínio. Dimensione após testar o volume de usuários e arquivos.
2. Clone o repositório, copie `.env.example` para `.env` e configure senhas fortes e exclusivas. Restrinja o acesso ao arquivo.
3. Defina `APP_ORIGIN=https://app.seudominio.com.br` e `COOKIE_SECURE=true`.
4. Execute `docker compose up -d --build`.
5. Configure Nginx/Caddy no host para terminar HTTPS e encaminhar para `127.0.0.1:8080`. Obtenha e renove certificado TLS. Não publique o banco nem a API diretamente.
6. Cadastre a empresa e a chave Pix, faça os testes abaixo e só então convide responsáveis.

O proxy interno substitui `X-Forwarded-For` pelo endereço observado. Se houver outro proxy na frente, preserve o IP real apenas de proxies confiáveis e ajuste a configuração antes de atender muitos usuários. Na configuração padrão, o limite de 10 tentativas de login a cada 15 minutos pode ser compartilhado entre usuários atrás do mesmo endereço/proxy. A V1 usa um processo de API; para várias réplicas, mova o limitador de tentativas para armazenamento compartilhado.

Não há chaves de serviços no frontend. SMTP e senhas pertencem ao ambiente do servidor. A recuperação de senha requer `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_USER` e `SMTP_PASSWORD`, usando STARTTLS. Sem SMTP, o aplicativo informa que a recuperação está indisponível. Não há envio automático de cobranças por e-mail nesta versão.

## Instalação PWA

Em produção, exige HTTPS. No Android, use o menu de instalação do navegador. No iPhone, abra no Safari e use Compartilhar → Adicionar à Tela de Início. O projeto fornece o manifesto, os ícones e o service worker; a validação nos aparelhos reais deve ser feita após publicação. O aplicativo não usa internet em segundo plano para processar pagamentos. A central de avisos é atualizada enquanto o aplicativo está aberto; não há push nesta versão.

## Backups

Volumes Docker são persistência, não são backup. Faça cópias regulares do banco e dos comprovantes para armazenamento separado e protegido. Exemplo em shell Linux, a partir do diretório do projeto:

```bash
mkdir -p backups
docker compose exec -T db pg_dump -U school -d school -Fc > backups/school.dump
```

Para copiar os comprovantes sem depender do nome interno do volume:

```bash
docker compose cp api:/data/receipts backups/receipts
```

Pause gravações durante o backup para que banco e arquivos correspondam ao mesmo instante. Agende as cópias no servidor e teste uma restauração em ambiente separado. Não publique backups no GitHub. Defina retenção, criptografia e acesso conforme a operação. Atualizações futuras de esquema precisam de migração e backup antes de aplicação.

## Verificação antes do uso real

- Login, logout, recuperação por SMTP e troca de senha.
- Conta de cada responsável vendo somente os próprios registros, inclusive download de arquivos.
- Comprovante, aprovação, recusa, pagamento parcial e estorno.
- Empresa, chave Pix e titular conferidos com o operador.
- HTTPS, instalação em Android/iPhone e layout em aparelhos reais.
- Reinício dos containers sem perda do banco e dos comprovantes.
- Backup e restauração do banco junto dos arquivos.

Colete somente os dados necessários. Não há CPF, documentos ou localização de crianças exigidos nesta versão. Antes do uso real, o operador deve definir seus avisos de privacidade, atendimento, retenção e processo de correção/exclusão. A aplicação não substitui o banco para confirmar recebimentos.
