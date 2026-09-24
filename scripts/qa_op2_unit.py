"""
QA local (offline, sem DB) da OP2 ImprimirEvolucao — executa os casos do bloco
QA-A do plano: parse da planilha, mapas dos selects, parse de candidatos,
seleção em camadas e conversão de horas.

Uso: python qa_op2_unit.py  (a partir de backend/ ou worker/)
"""
import sys, os, io

FALHAS = []

def check(nome, cond, extra=""):
    status = "OK " if cond else "FAIL"
    print(f"[{status}] {nome}" + (f" — {extra}" if extra else ""))
    if not cond:
        FALHAS.append(nome)

# ─── 1) parse_planilha (service do backend) ────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.evolucao_service import parse_planilha, JANELA_DIAS

with open(r"C:\dev\clmf_hub_basic\evolucoes\evolucoes_2259525.xlsx", "rb") as f:
    payloads, erros = parse_planilha(f.read())

check("parse_planilha: sem erros de linha", erros == [], str(erros[:3]))
check("parse_planilha: 37 jobs (2 pacientes × datas)", len(payloads) == 37, f" obtido={len(payloads)}")

# esperado: 4251 → datas 12/01, 06/01, 13/01, 27/01 (4 jobs); 3602 → 33 datas
jobs_4251 = [p for p in payloads if p["idPaciente"] == 4251]
jobs_3602 = [p for p in payloads if p["idPaciente"] == 3602]
check("parse_planilha: paciente 4251 tem 4 jobs", len(jobs_4251) == 4, str([j["dataExec"] for j in jobs_4251]))
check("parse_planilha: paciente 3602 tem 33 jobs", len(jobs_3602) == 33, f"obtido={len(jobs_3602)}")

# job 4251/2026-01-12: 2 itens (guia 67575935, profs 3227 e 193), horas 0700 e 0800
job_12 = next(p for p in payloads if p["idPaciente"] == 4251 and p["dataExec"] == "2026-01-12")
itens_12 = sorted(job_12["itens"], key=lambda i: i["ID_prof"])
check("parse_planilha: job 2026-01-12 tem 2 itens", len(itens_12) == 2)
check("parse_planilha: horas em hhmm",
      {i["ID_prof"]: i["horas"] for i in itens_12} == {193: ["0800"], 3227: ["0700"]},
      str([(i["ID_prof"], i["horas"]) for i in itens_12]))
check("parse_planilha: janela ±30d", job_12["dataInicial"] == "2025-12-13" and job_12["dataFinal"] == "2026-02-11",
      f'{job_12["dataInicial"]}..{job_12["dataFinal"]}')
check("parse_planilha: CodTerapia/Profissional fora do payload",
      all(set(i) == {"guia", "ID_prof", "terapia", "horas"} for p in payloads for i in p["itens"]))
check("parse_planilha: itens com 2 horas agrupadas",
      any(len(i["horas"]) == 2 for p in payloads for i in p["itens"] if p["idPaciente"] == 3602 and p["dataExec"] == "2026-01-12"))
# ordenação por paciente (jobs do mesmo paciente consecutivos — afinidade)
ords = [p["idPaciente"] for p in payloads]
check("parse_planilha: jobs ordenados por paciente (ids consecutivos p/ afinidade)",
      ords == sorted(ords, key=lambda x: (x, ords.index(x) if False else 0)) and
      sum(1 for a, b in zip(ords, ords[1:]) if a != b) == 1, str(ords[:8]))

# ─── 2/3/4) funções puras do scraper ───────────────────────────────────────
sys.path.insert(0, r"C:\dev\clmf_hub_basic\worker\Worker")
from clmf_scraper import (parse_select_options, parse_candidatos, selecionar_candidatos,
                          _norm_texto, _input_value, CLMFScraper)

# select com &raquo; (fixture da spec)
select_html = """
<select name="profissional_id" id="profissional_id" class="select2">
    <option value=''>[Selecionar profissional]</option>
    <option value='1643'>&raquo;  DANIELLA  SANDES PEREIRA </option>
    <option value='658'>&raquo; ADNEIA BARRETO GOMES VALADAO</option>
    <option value='772'>&raquo; RAYANE KOJIMA PIMENTEL</option>
</select>"""
opts = parse_select_options(select_html)
check("select: 772 → RAYANE KOJIMA PIMENTEL", opts.get(772) == "RAYANE KOJIMA PIMENTEL", str(opts))
check("select: 1643 com espaços colapsados", opts.get(1643) == "DANIELLA SANDES PEREIRA")

# candidatos (fixture da spec: aba_atividade_single)
html_fixture = """
<tr class="aba_atividade_single" id="1059571">
    <td class="al_center"><input type="checkbox" id="atendimentoId1059571" name="imprimirItem" class="confirmado" value="1059571"></td>
    <td class="al_center">27/07/2026</td>
    <td class="al_center">18:00</td>
    <td class="al_center"><input type="date" id="novaData_1059571" name="novaData" class="" value="2026-08-20" onblur="gravarNovaData(this.value,1059571)"></td>
    <td class="al_center"><input type="time" id="horaInicial_1059571" name="atendimento_hr_inicial" class="" value="10:00" onblur="gravarHorario(this.value,1059571,'I')"></td>
    <td class="al_center"><input type="time" id="horaFinal_1059571" name="atendimento_hr_final" class="" value="11:00" onblur="gravarHorario(this.value,1059571,'F')"></td>
    <td class="al_center">Fonoaudiologia</td>
    <td class="al_center">RAYANE KOJIMA PIMENTEL</td>
    <td class="al_center">1</td>
</tr>
<tr class="aba_atividade_single" id="1059572">
    <td class="al_center"><input type="checkbox" id="atendimentoId1059572" name="imprimirItem" class="confirmado" value="1059572"></td>
    <td class="al_center">27/07/2026</td>
    <td class="al_center">18:00</td>
    <td class="al_center"><input type="date" id="novaData_1059572" name="novaData" class="" value="" onblur="gravarNovaData(this.value,1059572)"></td>
    <td class="al_center"><input type="time" id="horaInicial_1059572" name="atendimento_hr_inicial" class="" value="" onblur="gravarHorario(this.value,1059572,'I')"></td>
    <td class="al_center"><input type="time" id="horaFinal_1059572" name="atendimento_hr_final" class="" value="" onblur="gravarHorario(this.value,1059572,'F')"></td>
    <td class="al_center">Fonoaudiologia</td>
    <td class="al_center">RAYANE KOJIMA PIMENTEL</td>
    <td class="al_center">1</td>
</tr>
<tr class="aba_atividade_single" id="1059573">
    <td class="al_center"><input type="checkbox" id="atendimentoId1059573" name="imprimirItem" class="confirmado" value="1059573"></td>
    <td class="al_center">28/07/2026</td>
    <td class="al_center">09:00</td>
    <td class="al_center"><input type="date" id="novaData_1059573" name="novaData" class="" value="" onblur="gravarNovaData(this.value,1059573)"></td>
    <td class="al_center"><input type="time" id="horaInicial_1059573" name="atendimento_hr_inicial" class="" value="" onblur="gravarHorario(this.value,1059573,'I')"></td>
    <td class="al_center"><input type="time" id="horaFinal_1059573" name="atendimento_hr_final" class="" value="" onblur="gravarHorario(this.value,1059573,'F')"></td>
    <td class="al_center">Psicologia</td>
    <td class="al_center">OUTRA PESSOA</td>
    <td class="al_center">1</td>
</tr>
<tr class="aba_atividade_single" id="1059574">
    <td class="al_center"><input type="checkbox" id="atendimentoId1059574" name="imprimirItem" class="confirmado" value="1059574"></td>
    <td class="al_center">27/07/2026</td>
    <td class="al_center">14:00</td>
    <td class="al_center"><input type="date" id="novaData_1059574" name="novaData" class="" value="" onblur="gravarNovaData(this.value,1059574)"></td>
    <td class="al_center"><input type="time" id="horaInicial_1059574" name="atendimento_hr_inicial" class="" value="" onblur="gravarHorario(this.value,1059574,'I')"></td>
    <td class="al_center"><input type="time" id="horaFinal_1059574" name="atendimento_hr_final" class="" value="" onblur="gravarHorario(this.value,1059574,'F')"></td>
    <td class="al_center">Psicologia</td>
    <td class="al_center">RAYANE KOJIMA PIMENTEL</td>
    <td class="al_center">1</td>
</tr>
<tr class="aba_atividade_single" id="1059575">
    <td class="al_center"><input type="checkbox" id="atendimentoId1059575" name="imprimirItem" class="confirmado" value="1059575"></td>
    <td class="al_center">30/07/2026</td>
    <td class="al_center">10:00</td>
    <td class="al_center"><input type="date" id="novaData_1059575" name="novaData" class="" value="" onblur="gravarNovaData(this.value,1059575)"></td>
    <td class="al_center"><input type="time" id="horaInicial_1059575" name="atendimento_hr_inicial" class="" value="" onblur="gravarHorario(this.value,1059575,'I')"></td>
    <td class="al_center"><input type="time" id="horaFinal_1059575" name="atendimento_hr_final" class="" value="" onblur="gravarHorario(this.value,1059575,'F')"></td>
    <td class="al_center">Fonoaudiologia</td>
    <td class="al_center">RAYANE KOJIMA PIMENTEL</td>
    <td class="al_center">1</td>
</tr>"""
# Fixture (data do job = 27/07/2026, terapia Fonoaudiologia, prof RAYANE):
#   1059571 — 27/07 Fono RAYANE  preenchido → camada 1 (não-vazio)
#   1059572 — 27/07 Fono RAYANE  vazio      → camada 1 (vazio)
#   1059573 — 28/07 Psic OUTRA   vazio      → camada 4
#   1059574 — 27/07 Psic RAYANE  vazio      → camada 3 (mesma data, outra terapia)
#   1059575 — 30/07 Fono RAYANE  vazio      → camada 4
cands = parse_candidatos(html_fixture, "aba_atividade_single")
check("candidatos: 5 linhas extraídas", len(cands) == 5, str([c["id"] for c in cands]))
c1 = next(c for c in cands if c["id"] == 1059571)
check("candidatos: campos preenchidos lidos", c1["novaData"] == "2026-08-20" and c1["horaInicial"] == "10:00" and c1["horaFinal"] == "11:00")
check("candidatos: vazio=False para 1059571 e True para 1059572",
      c1["vazio"] is False and next(c for c in cands if c["id"] == 1059572)["vazio"] is True)
check("candidatos: data_iso", c1["data_iso"] == "2026-07-27" and next(c for c in cands if c["id"] == 1059573)["data_iso"] == "2026-07-28")
check("candidatos: terapia/profissional normalizados",
      c1["terapia_norm"] == "FONOAUDIOLOGIA" and c1["profissional_norm"] == "RAYANE KOJIMA PIMENTEL",
      f'ter={c1["terapia_norm"]!r} prof={c1["profissional_norm"]!r}')

TER, PROF = "FONOAUDIOLOGIA", "RAYANE KOJIMA PIMENTEL"
D = "2026-07-27"

# N=2 na camada 1: 1059572 (vazio) vem ANTES de 1059571 (preenchido) e antes da
# camada 3 (1059574, vazio) — discrimina casamento de terapia/profissional
sel = selecionar_candidatos(cands, set(), D, TER, PROF, 2)
check("camada 1 completa com prioridade de campos vazios", [c["id"] for c in sel] == [1059572, 1059571], str([c["id"] for c in sel]))

# N=3: completa com camada 3 (mesma data, outra terapia) ANTES de outras datas (camada 4)
sel = selecionar_candidatos(cands, set(), D, TER, PROF, 3)
check("completa entre camadas (1→3) antes de outras datas (4)",
      [c["id"] for c in sel] == [1059572, 1059571, 1059574], str([c["id"] for c in sel]))

# N=5: tudo — camada 4 ordena por vazio e id (1059573 antes de 1059575)
sel = selecionar_candidatos(cands, set(), D, TER, PROF, 5)
check("camada 4: vazio+id entre outras datas", [c["id"] for c in sel] == [1059572, 1059571, 1059574, 1059573, 1059575],
      str([c["id"] for c in sel]))

# prof_norm=None (ID_prof ausente no select): 71/72 caem para camada 2, ainda
# antes de outra terapia (3) e outras datas (4)
sel = selecionar_candidatos(cands, set(), D, TER, None, 3)
check("prof_norm=None: data+terapia (camada 2) antes de outra terapia (camada 3)",
      [c["id"] for c in sel] == [1059572, 1059571, 1059574])

# data do job sem nenhum candidato na data: tudo vira camada 4 (vazio+id)
sel = selecionar_candidatos(cands, set(), "2026-08-10", TER, PROF, 1)
check("camada 4 (outras datas) quando data do job não bate", [c["id"] for c in sel] == [1059572], str([c["id"] for c in sel]))

# candidato já usado nunca é reoferecido
sel = selecionar_candidatos(cands, {1059572}, D, TER, PROF, 2)
check("usados excluídos da seleção", [c["id"] for c in sel] == [1059571, 1059574], str([c["id"] for c in sel]))

# pool menor que N → devolve o que tem (chamador decide PENDENTE)
sel = selecionar_candidatos(cands, {1059571, 1059572}, D, TER, PROF, 4)
check("pool insuficiente devolve parcial", [c["id"] for c in sel] == [1059574, 1059573, 1059575],
      str([c["id"] for c in sel]))

# ─── 5) horas ──────────────────────────────────────────────────────────────
hi, hf = CLMFScraper._hora_hhmm_para_request("0700")
check("hora 0700 → 07:00/08:00", hi == "07:00" and hf == "08:00", f"{hi}/{hf}")
hi, hf = CLMFScraper._hora_hhmm_para_request("2330")
check("edge 2330 → 23:30/00:30", hi == "23:30" and hf == "00:30", f"{hi}/{hf}")

print()
print("RESULTADO:", "TODOS OS CASOS OK" if not FALHAS else f"{len(FALHAS)} FALHA(S): {FALHAS}")
sys.exit(1 if FALHAS else 0)
