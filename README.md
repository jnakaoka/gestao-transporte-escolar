# Rota Escolar — Gestão de Transporte Escolar

Primeira versão funcional de uma PWA para controle de mensalidades no Brasil. React + Vite, FastAPI + SQLAlchemy, PostgreSQL e Docker Compose. A aplicação é independente dos projetos Unidal e Bombistas.

## Funcionalidades

- Login por cookie HttpOnly, logout, troca de senha e recuperação por e-mail quando SMTP estiver configurado.
- Administração: responsáveis, alunos, contratos com desconto, geração de mensalidades, registro de pagamentos parciais, estornos com motivo, conferência de comprovantes, exportação CSV e consulta de auditoria pela API.
- Responsável: consulta exclusiva dos próprios alunos, contratos, mensalidades e pagamentos; chave Pix, envio e download de comprovantes e central de avisos.
- Mensalidades do mês atual são geradas ao iniciar a API e a cada hora, conforme o contrato ativo. Meses anteriores ou futuros dentro do contrato podem ser gerados manualmente. A chave única contrato/mês impede duplicações.
- Comprovantes PDF/JPG/PNG de até 5 MB, guardados em volume persistente fora da pasta pública e acessíveis apenas por autorização.
- PWA com ícones, manifesto e tela sem conexão. Não há gravação offline nem cache de dados financeiros.
- Avisos dentro do aplicativo e link de WhatsApp com mensagem para revisão e envio manual. Push e e-mails automáticos de cobrança não foram implementados. SMTP é usado apenas para recuperação de senha.

## Rodar no Windows com Docker Desktop

Requisitos: Git, Docker Desktop em execução e o terminal PowerShell. O Docker Compose deve estar disponível.

```powershell
git clone https://github.com/jnakaoka/gestao-transporte-escolar.git
cd gestao-transporte-escolar
Copy-Item .env.example .env
notepad .env
```

Preencha `ADMIN_EMAIL`, `ADMIN_PASSWORD` (mínimo 12 caracteres) e `DB_PASSWORD` (letras e números, para a URL do banco). Não use as senhas de exemplo em produção. Mantenha `APP_ORIGIN=http://localhost:8080` no teste local.

```powershell
docker compose up -d --build
docker compose logs -f api
```

Abra **http://localhost:8080**. A conta administrativa será criada a partir do `.env` somente no primeiro início. Depois de criada, alterar `ADMIN_PASSWORD` no `.env` não modifica a senha da conta: use Configurações no app.

Fluxo recomendado:

1. Entre como administrador e preencha Configurações → empresa, chave Pix e titular.
2. Cadastre um responsável com e-mail e senha inicial. Entregue a senha por canal seguro; ele pode alterá-la na própria conta.
3. Cadastre um ou mais alunos vinculados ao responsável.
4. Cadastre um contrato por aluno, com valor, desconto, vencimento e período.
5. Confira Mensalidades. O mês atual é gerado automaticamente se estiver dentro do período. Se necessário, escolha outra competência e clique em Gerar mês.
6. Em uma janela privada, entre com o responsável e envie um comprovante.
7. Na conta administrativa, abra Comprovantes, confira o arquivo e o extrato bancário e confirme ou recuse. O responsável verá o resultado e o saldo atualizado.

Atualizar:

```powershell
git pull
docker compose up -d --build
```

Parar sem apagar dados: `docker compose down`. **Não use `docker compose down -v`: a opção `-v` apaga os volumes do banco e dos comprovantes.**

## Regras financeiras

- Valores em centavos inteiros, sem cálculo financeiro com ponto flutuante no banco.
- Um contrato por aluno por período, sem sobreposição. O responsável pode ter vários alunos.
- Mensalidade = valor mensal − desconto fixo. O desconto deve ser menor que o valor mensal.
- Cobrança integral em cada mês que intersecta o período; não há proporcionalidade por dias, regra de férias ou juros automáticos.
- Dia 29/30/31 é ajustado ao último dia de meses menores.
- Mudanças de situação não alteram cobranças históricas. Contrato pausado/encerrado, aluno inativo ou responsável inativo não gera novas cobranças.
- Reativar permite a geração dos meses ainda não cobrados; o sistema não guarda períodos históricos de pausa. Não gere manualmente meses que não devem ser cobrados.
- Comprovante enviado não é pagamento confirmado. Apenas um comprovante pendente por mensalidade; após recusa, pode reenviar.
- Pagamentos acima do saldo ou com data futura são recusados. Conferência repetida e estorno repetido são bloqueados.
- Cancelamento exige ausência de pagamentos ativos e comprovantes pendentes. Estorno preserva o registro e devolve o valor ao saldo.
- Pix é uma chave exibida para cópia. Não há integração bancária, conciliação automática, QR dinâmico nem emissão fiscal.

## Desenvolvimento e testes

Backend (Python 3.12):

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest -q tests
$env:ADMIN_EMAIL="admin@example.com"
$env:ADMIN_PASSWORD="escolha-uma-senha-forte"
$env:APP_ORIGIN="http://localhost:5173"
python -m uvicorn app.main:app --reload
```

O desenvolvimento usa SQLite por padrão; Compose usa PostgreSQL. Em outro terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Abra http://localhost:5173. Build: `npm run build`. Os testes cobrem isolamento entre responsáveis, privilégios, geração idempotente, meses curtos, pagamentos parciais, estorno, comprovantes, reset de senha, CSV e bloqueios de autenticação. Consulte `docs/HOSPEDAGEM.md` antes de usar dados reais.

## Estado da entrega

Código para testes locais; nenhuma hospedagem externa ou domínio é provisionado por este repositório. Push, cobrança automática por e-mail, integração bancária e multiempresa ficam para evolução. A estrutura inicial cria tabelas automaticamente; alterações futuras de esquema exigirão migrações versionadas antes de atualizar uma base existente.
