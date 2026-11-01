"""How-to articles: the built-in answers when no AI is set up, and what the AI is told to answer from."""
import re
from dataclasses import dataclass

from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class Article:
    key: str
    title: object
    body: object
    url_name: str = ""
    keywords: str = ""  # extra search words, English and Bangla


ARTICLES = [
    Article("sell", _("Selling at the POS"), _(
        "Open POS from the menu and open your cash register with the cash in the drawer. Search or scan medicines, change the unit "
        "(strip, box) and quantity on each line, pick a customer if needed, then press Pay (F4), enter the payments and Complete sale. "
        "Print the receipt from the box that follows."), "sales:pos", "pos sell sale bill invoice scan barcode cart বিক্রি বিল পস"),
    Article("discount", _("Discounts and offers"), _(
        "Type a discount on a line or for the whole bill (amount or %) in the POS. A customer's default discount fills in by itself. "
        "Running promotions (Sales → Promotions) give their discount automatically to the medicines they cover."),
        "sales:promotions", "discount offer promotion percent ছাড় অফার ডিসকাউন্ট"),
    Article("due", _("Selling on due and collecting it"), _(
        "Pick the customer at the POS and pay less than the bill: the rest goes on their due (within their credit limit). "
        "Collect it later from Customers → Collect due, or see what each customer owes on their page."),
        "customers:receipt_create", "due credit owe collect baki বাকি পাওনা আদায়"),
    Article("returns", _("Taking back a sale (returns)"), _(
        "Open the sale (Sales → Sale list), press Return, enter how many units come back and how much money you give back. "
        "Stock goes back to its batch and the customer's due or your cash is corrected."), "sales:list", "return refund back ফেরত রিটার্ন"),
    Article("register", _("Opening and closing the cash register"), _(
        "Each cashier opens a register with the starting cash before selling. At the end of the day close it from Sales → Cash registers "
        "and count the cash: MediPOS shows what was expected and any shortage or excess."),
        "sales:registers", "cash register drawer close z report shortage ক্যাশ রেজিস্টার হিসাব"),
    Article("medicines", _("Adding medicines"), _(
        "Medicines → Add medicine for one at a time, or pick from the ThingBD catalog of Bangladeshi medicines, or import an Excel file. "
        "Set the units (piece, strip, box), prices and the low-stock alert level."), "items:list", "medicine product item add import catalog ওষুধ পণ্য"),
    Article("stock", _("Stock, batches and expiry"), _(
        "Stock is kept per outlet and per batch with its expiry date; sales take the batch that expires first. "
        "Stock → Stock list shows what you have, Low stock what to reorder, and Expiring what to sell or return soon."),
        "stock:list", "stock batch expiry expired low reorder স্টক মেয়াদ ব্যাচ"),
    Article("purchase", _("Buying from suppliers"), _(
        "Purchases → Add purchase: pick the supplier, add medicines with batch, expiry, quantity and cost, then Receive to put them in stock. "
        "Pay the supplier at once or later from Suppliers → Supplier payments."), "purchases:create", "purchase buy supplier receive ক্রয় কেনা সাপ্লায়ার"),
    Article("transfer", _("Moving stock between outlets"), _(
        "Inventory → Transfers: send stock from one outlet; it is on its way until the other outlet presses Receive."),
        "inventory:transfers", "transfer branch outlet move স্থানান্তর শাখা"),
    Article("damage", _("Damaged and expired stock"), _(
        "Inventory → Damage & expired: write off the batches that can't be sold. The loss goes to the books."),
        "inventory:damages", "damage expired write off loss নষ্ট মেয়াদোত্তীর্ণ"),
    Article("count", _("Counting stock"), _(
        "Inventory → Stock counts: count the shelves, enter what you found, and MediPOS adjusts the stock and shows what was missing or extra."),
        "inventory:counts", "count stock take physical adjustment গণনা মজুত"),
    Article("customers", _("Customers and loyalty points"), _(
        "Customers → Customer list holds names, phones, credit limits and dues. With loyalty points on (Loyalty points → Loyalty settings), "
        "customers earn points on each bill and spend them at the POS."), "customers:list", "customer loyalty points গ্রাহক পয়েন্ট"),
    Article("messages", _("SMS, WhatsApp and email"), _(
        "Messages → Message settings: send bills by SMS after a sale, remind customers of their due, and get a daily stock alert. "
        "SMS use MediPOS SMS credit (ask ThingBD) or your own SMS provider. Campaigns send one message to many customers."),
        "messaging:settings", "sms whatsapp email message campaign credit মেসেজ এসএমএস"),
    Article("reports", _("Reports"), _(
        "Reports has sales, profit, stock, purchases, customer and supplier ledgers, staff and message reports. Pick dates and export to Excel."),
        "reports:index", "report profit sales summary excel রিপোর্ট লাভ"),
    Article("accounts", _("Expenses and the books"), _(
        "Record rent, bills and salaries in Accounts → Income & expenses. Sales and purchases go to the books by themselves; "
        "see Profit & loss, Balance sheet and Cash flow under Accounting."), "accounting:expenses", "expense accounts profit loss balance খরচ হিসাব"),
    Article("staff", _("Staff, roles and permissions"), _(
        "People → Employees to add staff with a login; Roles & permissions decides what each role can see and do. "
        "The owner can always do everything."), "people:employees", "staff employee role permission user কর্মচারী রোল অনুমতি"),
    Article("attendance", _("Attendance and payroll"), _(
        "People → Attendance for the daily sheet, shifts and allowed shop networks; staff check in from their dashboard. "
        "Payroll works out the month's salaries with absences, commission and advances."), "hr:attendance", "attendance payroll salary shift হাজিরা বেতন শিফট"),
    Article("printing", _("Invoices and printing"), _(
        "Shop setup → Invoice & printing: paper size (58 mm, 80 mm, A4), what shows on the bill, Bangla bills, QR code, and printing the receipt "
        "by itself after each sale."), "setup:invoice", "print printer receipt invoice paper qr প্রিন্ট রসিদ ইনভয়েস"),
    Article("audit", _("Branch audits and the activity log"), _(
        "Accounts → Audit → Branch audits: head office checks a branch's sales, cash, stock and books for a period, marks a checklist and "
        "signs it off. The Activity log shows who changed what."), "audit:branches", "audit branch check activity log অডিট শাখা"),
    Article("backup", _("Exporting and backing up data"), _(
        "Shop setup → Data export & backup: download any list to Excel, or a full backup of everything."), "backup:data", "export backup excel download ব্যাকআপ এক্সপোর্ট"),
    Article("language", _("Language and password"), _(
        "Switch between English and বাংলা from the top bar. Change your password from your profile (top right). "
        "Forgot it? Use “Forgot password?” on the login page."), "accounts:profile", "language bangla english password ভাষা পাসওয়ার্ড বাংলা"),
]

# Split on spaces and punctuation, not \w: Bangla vowel signs aren't "word" characters to Python's \w.
WORD = re.compile(r"[^\s.,!?;:()\[\]{}\"'“”‘’/\|।\-–—→]+")


def url_of(article):
    try:
        return reverse(article.url_name) if article.url_name else ""
    except NoReverseMatch:
        return ""


STOP = set(
    "the and for how what what's can you with this that from are our your does will into they then when where which have has "
    "not but all any its was were want need please tell about there here should would could do does did get make".split()
) | {"কী", "কি", "কীভাবে", "কিভাবে", "করব", "করবো", "করতে", "কোথায়", "আমি", "আমার", "এবং", "থেকে", "হবে", "কেন", "একটা", "একটি"}
MIN_SCORE = 2  # one keyword or title word; words that only appear in the text aren't enough on their own


def search(question, limit=3):
    """The articles that best match a question, best first: keywords count most, then title words, then the text."""
    words = {w for w in WORD.findall((question or "").lower()) if (len(w) > 2 or not w.isascii()) and w not in STOP}
    if not words:
        return []
    scored = []
    for article in ARTICLES:
        keywords = set(WORD.findall(article.keywords.lower()))
        title = set(WORD.findall(str(article.title).lower()))
        body = set(WORD.findall(str(article.body).lower()))
        score = 3 * len(words & keywords) + 2 * len(words & title) + len(words & body)
        if score >= MIN_SCORE:
            scored.append((score, article))
    scored.sort(key=lambda pair: -pair[0])
    return [article for _score, article in scored[:limit]]


def as_text(article):
    url = url_of(article)
    return f"**{article.title}**\n{article.body}" + (f"\n[{_('Open it')}]({url})" if url else "")
