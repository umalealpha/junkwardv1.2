"""
python manage.py seed_agent_banks [--commit]

One-time load of the agent bank details Motlatsi seeded into her v6 tool
(SEED_BANKS, sourced from Unicoin_Final_Commission_June_2026). Idempotent:
- creates the Agent if missing (name = natural key, as in her tool)
- skips rows with a blank / '0' account number (they stay "missing" so the
  Banking coverage count remains truthful)
- NEVER overwrites an existing AgentBankAccount (manual captures win);
  only fills a blank bank_name like her tool's merge did
Dry-run by default; --commit writes.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from agent_portal.models import Agent, AgentBankAccount

SEED = {'Lone Lefa Morolong': {'account': '62867600671', 'branch': '288767', 'bank': 'FNB Botswana'}, 'Morati Segwe': {'account': '62777545230', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Maatla Boletswane': {'account': '62776765160', 'branch': '283767', 'bank': 'FNB Botswana'}, 'Gaongalelwe Nage': {'account': '63205718076', 'branch': '289567', 'bank': 'FNB Botswana'}, 'Mosimanegape Mojara': {'account': '62820570077', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Katlego Masilo': {'account': '62820991265', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Thuto Mosimanewakgomo': {'account': '63099321879', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Kesegofetse Jim': {'account': '63012472104', 'branch': '281267', 'bank': 'FNB Botswana'}, 'Gofaone Mothibi': {'account': '63167488593', 'branch': '283567', 'bank': 'FNB Botswana'}, 'Gofiwa Elias': {'account': '63170351604', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Jessica Nkala': {'account': '63115107228', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Wadza Joshua': {'account': '63007717771', 'branch': '288467', 'bank': 'FNB Botswana'}, 'Thabang Amos Thantshi': {'account': '63201653656', 'branch': '283567', 'bank': 'FNB Botswana'}, 'George Modimoosi': {'account': '63139176788', 'branch': '288267', 'bank': 'FNB Botswana'}, 'Kutlo Koma': {'account': '63076869941', 'branch': '284567', 'bank': 'FNB Botswana'}, 'Batsile Nawe': {'account': '63196765912', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Boemo Mavuka': {'account': '62859559852', 'branch': '288267', 'bank': 'FNB Botswana'}, 'Katlego Cave': {'account': '63123903296', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Leshoa Mosimakoko': {'account': '62914824603', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Refilwe Vanessah Boitumelo Ramphaleng': {'account': '63153624929', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Godi R Lucas': {'account': '62686426597', 'branch': '288967', 'bank': 'FNB Botswana'}, 'Amogelang Jennifer Makwinja': {'account': '63139491574', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Rachel Mufoyi': {'account': '0', 'branch': '0', 'bank': ''}, 'Tshepo Sesika': {'account': '62869808504', 'branch': '288967', 'bank': 'FNB Botswana'}, 'Gagothata Eric Pona': {'account': '63076048016', 'branch': '283767', 'bank': 'FNB Botswana'}, 'Pako Angela Mampane': {'account': '0', 'branch': '0', 'bank': ''}, 'Obakeng Tatenda Molefhe': {'account': '0', 'branch': '0', 'bank': ''}, 'Olorato Wally': {'account': '63097685970', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Amantle Ntshweu': {'account': '63081916216', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Obakeng T Molefe': {'account': '0', 'branch': '0', 'bank': ''}, 'Balekanye Seduledi': {'account': '63138039375', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Thato Barati Mogopa': {'account': '0', 'branch': '0', 'bank': ''}, 'Gorata Kebalepile': {'account': '62871245893', 'branch': '283567', 'bank': 'FNB Botswana'}, 'Kunyalala July': {'account': '62708540671', 'branch': '288267', 'bank': 'FNB Botswana'}, 'Kesego Thabiso Atang Mmolawa': {'account': '63149773079', 'branch': '281667', 'bank': 'FNB Botswana'}, 'Reabetswe Mookodi': {'account': '63200623593', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Peo Dilaolo': {'account': '0', 'branch': '0', 'bank': ''}, 'Katlego Molao': {'account': '63160365277', 'branch': '283567', 'bank': 'FNB Botswana'}, 'Motsei Mothoosele': {'account': '62843666465', 'branch': '283167', 'bank': 'FNB Botswana'}, 'Stellah Rakgomo': {'account': '63121139629', 'branch': '288967', 'bank': 'FNB Botswana'}, 'Balemogeng Jessica Kgotleng': {'account': '0', 'branch': '0', 'bank': ''}, 'Ramaphane Ramaphane': {'account': '63009008384', 'branch': '283767', 'bank': 'FNB Botswana'}, 'Tuduetso Alimah Mantirisi': {'account': '63181149048', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Alindo Govati': {'account': '63094942042', 'branch': '288367', 'bank': 'FNB Botswana'}, 'Pono Duduetsang Moagi': {'account': '0', 'branch': '0', 'bank': ''}, 'Norah Dibuile': {'account': '0', 'branch': '0', 'bank': ''}, 'Basetsana Poitshego': {'account': '63165921826', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Olga Koketso Rakgomo': {'account': '63146891288', 'branch': '281767', 'bank': 'FNB Botswana'}, 'Letso Chinambe': {'account': '63128706025', 'branch': '281867', 'bank': 'FNB Botswana'}, 'Olorato Kantini': {'account': '63067283803', 'branch': '285267', 'bank': 'FNB Botswana'}, 'Olorato Tshephang Wally': {'account': '63097685970', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Laone Grace Gomang': {'account': '63140379628', 'branch': '283567', 'bank': 'FNB Botswana'}, 'Batsile Fredah Nawe': {'account': '63196765912', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Jessica Kgotleng': {'account': '63070980107', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Pono Moagi': {'account': '62869086267', 'branch': '283567', 'bank': 'FNB Botswana'}, 'Thulaganyo Loago Setshogo': {'account': '62710987514', 'branch': '281767', 'bank': 'FNB Botswana'}, 'Olorato Motladiile': {'account': '63179383319', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Katlego Mosadi': {'account': '63160896686', 'branch': '281867', 'bank': 'FNB Botswana'}, 'Dimakatso Dipuo Moreetsi': {'account': '62833816583', 'branch': '288267', 'bank': 'FNB Botswana'}, 'Gaone Karema': {'account': '63073611387', 'branch': '283567', 'bank': 'FNB Botswana'}, 'Kelebogile Tshenolo': {'account': '63186047461', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Laone Thembi Baswetsang': {'account': '63160763330', 'branch': '281267', 'bank': 'FNB Botswana'}, 'Prisilla Mophakedi': {'account': '63205246895', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Loago Gaboyo': {'account': '63191334613', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Candy O Entaile': {'account': '62839749465', 'branch': '281967', 'bank': 'FNB Botswana'}, 'Kaone Marry Mosupi': {'account': '63192526970', 'branch': '1671', 'bank': 'FNB Botswana'}, 'Lebogang Pearl Modimako': {'account': '63190587255', 'branch': '282867', 'bank': 'FNB Botswana'}, 'botho resego batlhabanye': {'account': '63201116498', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Pontsho Shabana': {'account': '63149313015', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Nametsegang Nalebomo': {'account': '63190601302', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Onneile Molefhe': {'account': '63005766621', 'branch': '284567', 'bank': 'FNB Botswana'}, 'Gofaone Maiswe': {'account': '63156193822', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Letlotlo Mogale': {'account': '63115200345', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Rachel Mufoyi Mufoyi': {'account': '63107240953', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Resego Molefe': {'account': '63099147598', 'branch': '288367', 'bank': 'FNB Botswana'}, 'Thabo Obositswe Bathopi': {'account': '63095127685', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Cathrine Khali': {'account': '63016566698', 'branch': '288267', 'bank': 'FNB Botswana'}, 'Katlego Pauline Cave': {'account': '63123903296', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Shadrack Theba': {'account': '62645878440', 'branch': '283567', 'bank': 'FNB Botswana'}, 'Balekanye Kayla Thophego': {'account': '63078216249', 'branch': '288367', 'bank': 'FNB Botswana'}, 'Tapiwa Percy Selogelo': {'account': '63036953180', 'branch': '288467', 'bank': 'FNB Botswana'}, 'Naledi Modimootsile': {'account': '62928108449', 'branch': '740', 'bank': 'FNB Botswana'}, 'Kago B Snyman': {'account': '63205909352', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Lepa W Mosimakoko': {'account': '63206489139', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Karabo Gabanapelo': {'account': '63102869641', 'branch': '288467', 'bank': 'FNB Botswana'}, 'Refilwe Vanessah Ramphaleng': {'account': '63153624929', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Batani Kamununu': {'account': '63183487959', 'branch': '283567', 'bank': 'FNB Botswana'}, 'Katlo Pretty Seboka': {'account': '63097720833', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Marylyn T Y Ramolefhe': {'account': '1788765', 'branch': '29-34-67', 'bank': ''}, 'Kefilwe Pearl Takobana': {'account': '62922952438', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Cynthia Tlhaloso Rammiki': {'account': '63198673014', 'branch': '282867', 'bank': 'FNB Botswana'}, 'Charity Malebogo Daniel': {'account': '9060010000000', 'branch': '60367', 'bank': ''}, 'Olebogeng Matsididi': {'account': '62867001548', 'branch': '283567', 'bank': 'FNB Botswana'}, 'Thato Barati': {'account': '62800739677', 'branch': '283567', 'bank': 'FNB Botswana'}, 'Pako Mampane': {'account': '62487726287', 'branch': '283567', 'bank': 'FNB Botswana'}, 'Obakeng Tetanda': {'account': '63212396865', 'branch': '283567', 'bank': 'FNB Botswana'}, 'Karabo O': {'account': '\u200e1724048', 'branch': '35', 'bank': ''}, 'Thuso Kegontse': {'account': '63213102021', 'branch': '288267', 'bank': 'FNB Botswana'}, 'Karabo Anita Segokgo': {'account': '62918374802', 'branch': '282867', 'bank': 'FNB Botswana'}}


def _clean_account(v):
    # strip whitespace + Unicode format chars (one row carries a U+200E mark)
    return "".join(ch for ch in str(v or "").strip() if ch.isprintable() and not ch.isspace()).lstrip("‎")


class Command(BaseCommand):
    help = "Seed agent bank details from Motlatsi's v6 tool (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true")

    @transaction.atomic
    def handle(self, *args, **opts):
        created = updated_bankname = skipped_existing = skipped_no_account = 0
        for name, row in SEED.items():
            acct = _clean_account(row.get("account"))
            if acct in ("", "0"):
                skipped_no_account += 1
                continue
            agent, _ = Agent.objects.get_or_create(name=name.strip())
            bank = getattr(agent, "bank", None)
            if bank is None:
                AgentBankAccount.objects.create(
                    agent=agent, bank_name=(row.get("bank") or "").strip(),
                    account_number=acct, branch_code=str(row.get("branch") or "").strip(),
                    account_name=name.strip(),
                )
                created += 1
            elif not (bank.bank_name or "").strip() and (row.get("bank") or "").strip():
                bank.bank_name = row["bank"].strip()
                bank.save(update_fields=["bank_name", "updated_at"])
                updated_bankname += 1
            else:
                skipped_existing += 1
        line = (f"created={created} bankname_filled={updated_bankname} "
                f"kept_existing={skipped_existing} no_account_skipped={skipped_no_account}")
        if not opts["commit"]:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING(f"[dry-run, rolled back] {line}"))
        else:
            self.stdout.write(self.style.SUCCESS(line))
