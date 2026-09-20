"""utils/email_validator.py
Comprehensive email validation and anti-fake email verification for Nirvaha.

Protections provided:
1. Strict RFC Syntax Validation: Proper local and domain formatting, length restrictions,
   no double dots, valid TLDs (>= 2 chars).
2. Disposable / Burner Domain Blocking: Rejects throwaway email services (e.g. Mailinator,
   TempMail, GuerrillaMail, 10MinuteMail, Yopmail, SharkLasers, etc.).
3. Bogus / Placeholder Domain Blocking: Rejects fake or dummy domains (e.g. fake.com,
   test.com, asdf.com, dummy.com, spam.com, etc.).
4. Dummy / Patterned Username Detection: Rejects keyboard mash, placeholder usernames
   (e.g. test@, fake@, asdf@, qwerty@, aaaaaa@), and system role accounts (noreply@,
   mailer-daemon@).
5. Live DNS / MX Host Verification: Validates that the domain actually exists on the public
   internet with active mail exchange (MX) or address (A) records, detecting non-existent domains.
"""

import re
import socket
import logging

try:
    import dns.resolver
    HAS_DNSPYTHON = True
except ImportError:
    HAS_DNSPYTHON = False

logger = logging.getLogger(__name__)

# Strict RFC-compliant email regex
EMAIL_STRICT_RE = re.compile(
    r"^[a-zA-Z0-9]+([._%+-][a-zA-Z0-9]+)*@[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)*\.[a-zA-Z]{2,}$"
)

# Disposable / Temporary / Burner email providers
DISPOSABLE_DOMAINS = frozenset({
    "0815.ru", "10minutemail.co.uk", "10minutemail.com", "10minutemail.net",
    "20minutemail.com", "abcvg.com", "anonbox.net", "anonymbox.com",
    "antichef.com", "antichef.net", "armyspy.com", "binkmail.com",
    "bobmail.info", "bouncr.com", "bresnan.net", "burnermail.io",
    "byom.de", "chammy.info", "chogmail.com", "cool.fr.nf",
    "courriel.fr.nf", "courrieltemporaire.com", "crazymailing.com", "cuvox.de",
    "dayrep.com", "deadaddress.com", "devnullmail.com", "discard.email",
    "discardmail.com", "discardmail.de", "disposableaddress.com", "disposablemail.com",
    "disposamail.com", "dispostable.com", "dodgit.com", "drdrb.net",
    "dropmail.me", "dumpmail.de", "eelmail.com", "einrot.com",
    "emailfake.com", "emailondeck.com", "fakemail.net", "fakemailgenerator.com",
    "fakeinbox.com", "fastacura.com", "filzmail.com", "fleckens.hu",
    "freemail.ms", "generator.email", "getairmail.com", "getnada.com",
    "getonemail.com", "ghostlymail.com", "gishpuppy.com", "grr.la",
    "guerillamail.biz", "guerillamail.com", "guerillamail.de", "guerillamail.info",
    "guerillamail.net", "guerillamail.org", "guerrillamail.biz", "guerrillamail.com",
    "guerrillamail.de", "guerrillamail.info", "guerrillamail.net", "guerrillamail.org",
    "guerrillamailblock.com", "gustr.com", "hidemail.de", "incognitohost.com",
    "inboxbear.com", "inboxkitten.com", "instant-email.org", "jetable.org",
    "jourrapide.com", "kasmail.com", "klzlk.com", "letthemeatspam.com",
    "maildrop.cc", "maileater.com", "mailexpire.com", "mailforspam.com",
    "mailinater.com", "mailinator.com", "mailinator2.com", "mailmoat.com",
    "mailnull.com", "mailpick.biz", "mailscrap.com", "meltmail.com",
    "messagebeamer.de", "mintemail.com", "mohmal.com", "mohmal.im",
    "mycleaninbox.net", "mytemp.email", "mytempemail.com", "mytrashmail.com",
    "nada.ltd", "netcourrier.com", "no-spam.ws", "nomail.xl.cx",
    "noopmail.com", "noreply.org", "nospam.ze.tc", "nospam4.us",
    "nospamfor.us", "notmailinator.com", "nowmymail.com", "oneoffmail.com",
    "owlpic.com", "pookmail.com", "quickinbox.com", "rcpt.at",
    "reallymymail.com", "reconmail.com", "rhyta.com", "safe-mail.net",
    "safetymail.info", "sendspamhere.com", "sharklasers.com", "shortmail.net",
    "sirenten.com", "smailpro.com", "sneakemail.com", "sofort-mail.de",
    "sondo.be", "spam4.me", "spamavert.com", "spambob.com",
    "spambob.net", "spambob.org", "spambox.info", "spambox.us",
    "spamcannon.com", "spamcannon.net", "spamcon.org", "spamcorptastic.com",
    "spamcowboy.com", "spamday.com", "spamex.com", "spamfree24.org",
    "spamgourmet.com", "spamherelots.com", "spamhereplease.com", "spamhole.com",
    "spaminator.de", "spaml.com", "spaml.de", "spammotel.com",
    "spammutex.com", "spamnudge.com", "spamspot.com", "spamthisplease.com",
    "streetwisemail.com", "superrito.com", "suremail.info", "teleworm.us",
    "tempail.com", "tempemail.co", "tempemail.net", "tempinbox.co.uk",
    "tempinbox.com", "tempmail.com", "temp-mail.org", "tempmailo.com",
    "temporaryemail.net", "temporaryinbox.com", "tempr.email", "tempun.com",
    "thisisnotmyrealemail.com", "throwawayemailaddress.com", "throwawaymail.com",
    "tmailor.com", "trash-mail.com", "trashmail.com", "trashmail.me",
    "trashmail.net", "trumail.com", "umail.net", "veryrealemail.com",
    "wetrainbayarea.com", "wh4f.org", "willhackforfood.biz", "xoxy.net",
    "yopmail.com", "yopmail.fr", "yopmail.net", "yourtags.com",
    "zippymail.info", "zoemail.com",
})

# Bogus / Placeholder / Dummy domains
BOGUS_DOMAINS = frozenset({
    "fake.com", "test.com", "testing.com", "dummy.com", "dummyemail.com",
    "fakeemail.com", "asdf.com", "junk.com", "sample.com", "random.com",
    "trash.com", "spam.com", "nowhere.com", "foo.bar", "nobody.com",
    "example.org", "example.net", "mail.fake", "invalid.com", "nonexistent.com",
})

# Disallowed dummy usernames / role-based accounts for public citizen registration
BOGUS_USERNAMES = frozenset({
    "test", "fake", "dummy", "asdf", "qwerty", "junk", "temp", "throwaway",
    "random", "anon", "anonymous", "nobody", "noone", "nothing",
    "noreply", "no-reply", "postmaster", "mailer-daemon", "webmaster",
})

# Allowed mock / local domains in development & automated testing
LOCAL_TEST_DOMAINS = frozenset({
    "localhost", "example.com", "disaster.test", "disaster-response.local",
})


def is_valid_email_format(email: str) -> bool:
    """Strict RFC syntactic check for email addresses:
    Disallows double dots, leading/trailing periods or hyphens, excessive lengths,
    and invalid top-level domain labels."""
    if not email or len(email) > 254:
        return False
    if ".." in email or email.startswith(".") or email.endswith("."):
        return False
    if not EMAIL_STRICT_RE.match(email):
        return False
    parts = email.split("@")
    if len(parts) != 2:
        return False
    local, domain = parts
    if len(local) > 64 or not local or not domain:
        return False
    if domain.startswith("-") or domain.endswith("-") or domain.startswith("."):
        return False
    return True


# Exported alias for compatibility
is_valid_email = is_valid_email_format


def is_disposable_domain(domain: str) -> bool:
    """Returns True if the domain or any parent domain belongs to a known
    disposable/temporary email service."""
    d = domain.lower().strip()
    if d in DISPOSABLE_DOMAINS:
        return True
    parts = d.split(".")
    for i in range(1, len(parts) - 1):
        sub = ".".join(parts[i:])
        if sub in DISPOSABLE_DOMAINS:
            return True
    return False


def is_bogus_local_part(local: str) -> bool:
    """Detects dummy, placeholder, or keyboard-mash usernames (e.g. 'test', 'fake',
    'aaaaaa', '123456', 'asdf')."""
    l = local.lower().strip()
    if l in BOGUS_USERNAMES:
        return True
    # Repetitive single characters, e.g., 'aaaaaa', '111111'
    if re.match(r"^(.)\1{4,}$", l):
        return True
    # Simple keyboard walks
    if l in ("123456", "1234567", "12345678", "123456789", "qwertyuiop", "asdfghjkl"):
        return True
    return False


def check_domain_resolvable(domain: str, timeout: float = 2.5) -> bool:
    """Verifies that the email domain actually exists and has public DNS records
    capable of receiving email (MX records) or routing traffic (A/AAAA records).
    Returns False if domain does not exist or has no DNS entries."""
    d = domain.strip().lower()

    # If dnspython is available, check MX records first (standard for mail domains),
    # falling back to A records if the host handles direct delivery.
    if HAS_DNSPYTHON:
        try:
            resolver = dns.resolver.Resolver()
            resolver.lifetime = timeout
            try:
                mx_answers = resolver.resolve(d, "MX")
                if mx_answers:
                    return True
            except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                # Fallback to A record
                a_answers = resolver.resolve(d, "A")
                if a_answers:
                    return True
            except dns.resolver.Timeout:
                logger.warning("DNS MX/A resolution timed out for domain %s", d)
                # Fallback to socket getaddrinfo
                pass
        except Exception as e:
            logger.debug("dnspython error for %s: %s", d, e)

    # Standard library fallback via socket.getaddrinfo
    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        addrinfo = socket.getaddrinfo(d, None)
        return bool(addrinfo)
    except (socket.gaierror, socket.herror):
        return False
    except Exception as e:
        logger.warning("Socket lookup error for domain %s: %s", d, e)
        # If transient network error, assume resolvable to not block real users
        return True
    finally:
        socket.setdefaulttimeout(old_timeout)


def validate_email_for_signup(email: str, is_testing: bool = False) -> tuple[bool, str | None]:
    """Comprehensive multi-layer validator for citizen account signups.
    Returns:
        (True, None) if the email is genuine, valid, and acceptable.
        (False, reason_message) with a user-friendly error explanation if rejected.
    """
    if not email:
        return False, "Email address is required."

    cleaned = email.strip().lower()

    # 1. Syntax check
    if not is_valid_email_format(cleaned):
        return False, "Please enter a valid, active email address (e.g. name@example.com) to receive disaster alerts and verification codes."

    local, domain = cleaned.split("@")

    # 2. Disposable email check (burner domains)
    if is_disposable_domain(domain):
        return False, f"Disposable and temporary email addresses (@{domain}) are not permitted. Please use your permanent email address."

    # 3. Bogus domain check (placeholder domains)
    if domain in BOGUS_DOMAINS:
        return False, f"The email domain (@{domain}) is a placeholder or fake domain. Please use your real email address."

    # 4. Local part checks (no dummy/placeholder accounts)
    if is_bogus_local_part(local):
        return False, "Please use a genuine personal or business email address, not a placeholder or test username."

    # In testing mode or for local test domains, allow whitelisted test domains without DNS lookup
    if is_testing or domain in LOCAL_TEST_DOMAINS or domain.endswith(".local") or domain.endswith(".test"):
        return True, None

    # In non-testing mode, disallow example.com/net/org in real signups
    if domain in ("example.com", "example.net", "example.org"):
        return False, "Example domains cannot be used for registration. Please enter your real email address."

    # 5. Live DNS / MX check to verify domain exists
    if not check_domain_resolvable(domain):
        return False, f"The email domain '@{domain}' does not appear to exist or cannot receive mail. Please verify your email spelling."

    return True, None
