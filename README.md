# OpenWave

> A rede colaborativa de escuta de rádio regional por streaming para monitoramento de segurança pública, resposta a emergências e informação direta da fonte.

---

## 1. Nome e Frase

**OpenWave**: Uma plataforma colaborativa de streaming de áudio de rádio (VHF/UHF/P25/DMR) que transmite despachos e ocorrências locais em tempo real para cidadãos acompanharem a segurança do seu bairro sem intermediários.

---

## 2. A Dor

* **Quem sofre:** Cidadãos preocupados com a segurança de sua família e comércio, lideranças de bairro, comerciantes, motoristas de aplicativo e pessoas desconfiadas de coberturas sensacionalistas ou omitidas da imprensa tradicional.
* **Quando:** Durante ocorrências policiais no bairro (tiroteios, perseguições, cercos), emergências de bombeiros/defesa civil (incêndios florestais, enchentes, acidentes graves em rodovias) ou em momentos de tensão na cidade.
* **Quanto custa hoje:**
  * **Tempo e Risco:** Depender de notícias que demoram horas (ou dias) para sair no portal local, ou de matérias que chegam distorcidas e incompletas, perdendo a janela de decisão para evitar uma rota perigosa ou trancar o comércio.
  * **Dinheiro:** Comprar receptores digitais ou scanners caros (scanners analógicos/digitais ou dongles SDR calibrados custam de R$ 350 a R$ 2.000) e lidar com antenas no telhado.
  * **Frustração:** Grupos de WhatsApp cheios de pânico, correntes falsas e boatos, sem acesso à transmissão oficial crua e sem filtro.
* **Como resolvem hoje:** Ligam para parentes, acompanham grupos caóticos de mensagens cheios de "ouvi falar" ou esperam notas oficiais tardias da imprensa.

---

## 3. Persona

* **Nome fictício:** Marcos Rogério Antunes
* **Idade:** 42 anos
* **Rotina e Perfil:** Pequeno comerciante (dono de minimercado) e motorista de aplicativo nas horas vagas no interior paulista.
* **O que tenta fazer:** Quer saber imediatamente quando há perseguição, assalto a mão armada ou operação de resgate das viaturas perto de sua rota ou do seu comércio, para baixar as portas ou desviar antes de ser surpreendido.
* **O que atrapalha:** Não confia nas notícias locais que costumam abafar ou atrasar ocorrências para não gerar alarme, não entende nada de frequências de rádio e não tem dinheiro nem paciência técnica para montar um scanner SDR em casa.

---

## 4. Concorrentes e Alternativas

1. **Grupos de WhatsApp e Facebook ("Ocorrências da Cidade"):** Espalham pânico, áudios truncados sem data/hora confirmada e teorias da conspiração; não oferecem o áudio oficial das equipes de campo.
2. **Portais de Notícias Tradicionais:** Chegam atrasados, dependem de notas de assessoria filtradas e frequentemente omitem detalhes imediatos relevantes para a segurança do cidadão na rua.
3. **Scanners Físicos e Receptores RTL-SDR:** Exigem alto conhecimento de radiofrequência, cabos coaxiais, configuração de softwares complexos no computador e têm alcance limitado pelo relevo da casa do usuário.

---

## 5. Proposta de Valor

Saiba o que está acontecendo na segurança da sua cidade em tempo real, ouvindo o áudio direto da fonte sem ruído de intermediários, censura ou necessidade de comprar rádios caros.

---

## 6. Modelo de Receita com Stripe

O modelo financia a hospedagem em nuvem e ajuda os operadores locais voluntários (feeders) a manter seus rádios e antenas funcionando.

* **Plano Free (Comunitário):** R$ 0,00/mês
  * Acesso a 1 canal público prioritário de emergência/defesa civil da cidade.
  * Delay proposital de 10 minutos (para evitar saturação de tráfego).
  * Limite diário de 20 minutos de escuta contínua.
* **Plano Pro (Sentinela):** R$ 9,90/mês *(preço acessível de micro-assinatura)*
  * Áudio em tempo real (baixa latência) de todos os canais abertos monitorados na cidade (ocorrências rodoviárias, patrulhamento rural, bombeiros, trânsito).
  * Reprodutor contínuo sem limites de tempo no navegador ou celular.
  * Gravações das últimas 48 horas arquivadas por data e hora para ouvir o que aconteceu durante a madrugada.
  * Notificações de picos súbitos de atividade de rádio no canal monitorado.
  * *Por que assinam:* Pelo valor de um café por mês, o comerciante ou morador tem informação estratégica e antecipada para sua proteção pessoal.

---

## 7. Métricas de Sucesso

*(Metas calibradas para viabilidade rápida de validação acadêmica e piloto regional)*

* **Cadastros Ativos:** 30 a 50 usuários cadastrados na cidade/região piloto no primeiro mês.
* **Nós Ativos (Feeders):** 2 estações operando de forma estável (ex.: uma cobrindo tráfego/resgate e outra monitoramento geral).
* **Assinantes Pagantes:** 5 a 10 assinantes no Plano Pro durante a fase de validação (comprovando que há interesse real em pagar pela informação em tempo real).
* **MRR (Receita Recorrente Mensal):** R$ 50,00 a R$ 100,00/mês (suficiente para cobrir o custo da VPS e comprovar o fluxo do Stripe).
* **Engajamento:** Pelo menos 15 minutos médios de escuta por usuário em dias de incidentes relevantes na região.

---

## 8. Lean Canvas

| Bloco | Resumo |
|---|---|
| **Problema** | 1. Falta de informação em tempo real sobre ocorrências de segurança e emergência nas redondezas.<br>2. Imprensa tradicional lenta ou com notícias parciais e filtradas.<br>3. Complexidade técnica e custo alto de equipamentos receptores de rádio. |
| **Segmentos de Clientes** | 1. Comerciantes e motoristas que precisam de alerta antecipado de segurança.<br>2. Moradores que desconfiam da mídia tradicional e querem checar fatos na fonte.<br>3. Early adopters: Radioescutas e pessoas atentas à defesa civil de sua cidade. |
| **Proposta Única de Valor** | Informação de segurança local sem filtro: ouça os despachos de socorro e ocorrências da sua região em tempo real direto do navegador. |
| **Solução** | 1. Ingestão de áudio dos receptores locais (SDR/scanners).<br>2. Player web com canais organizados por tipo (Segurança, Resgate/Bombeiros, Rodovias).<br>3. Buffer de gravação das últimas 48h para conferência de incidentes passados. |
| **Canais** | 1. Grupos locais de alerta de trânsito e segurança de bairro.<br>2. Boca a boca entre comerciantes locais e motoristas.<br>3. Divulgação em redes sociais quando houver eventos meteorológicos ou fatos de grande repercussão. |
| **Fontes de Receita** | 1. Assinatura Pro mensal via Stripe (R$ 9,90/mês). |
| **Estrutura de Custos** | 1. Servidor VPS com Docker/Portainer.<br>2. Armazenamento de áudios no MinIO.<br>3. Taxas de transação por pagamento no Stripe. |
| **Métricas-Chave** | 1. 30 a 50 contas criadas.<br>2. 5 a 10 assinantes Pro.<br>3. Estabilidade do stream (tempo do canal online). |
| **Vantagem Injusta** | Captação regional direta com antenas e receptores instalados no próprio município, algo que redes globais ou portais genéricos de notícias não possuem. |

---

## 9. Mapa de Reaproveitamento da Arquitetura

| Peça do Projeto Anterior (Catálogo Tom Hanks) | O que vira no SaaS (OpenWave) | Detalhamento no Domínio |
|---|---|---|
| **Catálogo de Filmes (TMDB API / MariaDB)** | **Canais e Frequências de Segurança** | Cadastro e listagem de frequências ativas (Bombeiros/Resgate, Rodovias, Patrulha Rural, Defesa Civil), status do feed e cidade. |
| **Microsserviço de Login + RBAC** | **Níveis de Acesso e Permissões** | Papéis: `admin` (gestão do sistema), `feeder` (estação de rádio que envia áudio do scanner) e `listener` (cidadão ouvinte Free ou Pro). |
| **Logs e Auditoria (Redis)** | **Logs de Incidentes e Transmissão** | Registro de status de conexão dos feeds de rádio, contagem de picos de ouvintes simultâneos e histórico de sessões. |
| **Upload e Perfil (MinIO)** | **Storage de Gravações de Ocorrências** | Armazenamento de trechos de áudio compactados (.mp3/.ogg) das transmissões captadas para consulta retroativa pelo plano Pro. |
| **Plano Premium (Stripe)** | **Assinatura Ouvinte Pro (Sentinela)** | Processamento da cobrança recorrente de R$ 9,90/mês e liberação de canais ao vivo sem delay e histórico completo via webhook. |
| **Swagger / CI/CD / Observabilidade** | **Documentação da API e Pipeline** | Endpoints de publicação de áudio documentados, health checks dos containers e deploy contínuo via Portainer. |

---

Projeto acadêmico orientado pelo Prof. [@siriani](https://github.com/siriani).
