"""Tests for the public-site items DPO requires before issuing live credentials.

The checklist DPO sent is: a refund/cancellation policy, the physical location of
the business, social media icons linking to the real pages, every product with a
description and a price, an About us, and a published onboarding process.

These are tested because they are easy to lose. A footer link survives a template
refactor looking perfectly fine while pointing at a page that 404s, and nobody
notices until an acquirer does.
"""
import re

from django.test import TestCase
from django.urls import reverse

from monitor.models import LegalDocument, Package


class PublicComplianceTests(TestCase):
    def setUp(self):
        self.landing = self.client.get(reverse('monitor:home'))
        self.html = self.landing.content.decode()

    # ── 1. Refund / cancellation policy ──────────────────────────────────────
    def test_refund_policy_is_published_and_readable(self):
        response = self.client.get(reverse('monitor:legal_document', args=['refund']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Refund')
        # The substance DPO looks for: how to cancel, and how to get money back.
        body = response.content.decode().lower()
        self.assertIn('cancel', body)
        self.assertIn('refund', body)

    def test_refund_policy_is_linked_from_the_footer(self):
        self.assertContains(self.landing, reverse('monitor:legal_document', args=['refund']))

    def test_refund_policy_is_not_added_to_the_onboarding_consent_gate(self):
        """It is a statement of what we do, not a permission a user grants.
        Putting it in the gate would force every existing account to re-accept."""
        from monitor import legal
        self.assertNotIn('refund', legal.REQUIRED_DOC_TYPES)
        self.assertIn('refund', legal.PUBLIC_DOC_TYPES)
        self.assertFalse(LegalDocument.current('refund').requires_acceptance)

    # ── 2. Physical location ─────────────────────────────────────────────────
    def test_landing_page_states_the_physical_address(self):
        self.assertContains(self.landing, 'Plot 59065')
        self.assertContains(self.landing, 'Gaborone')

    # ── 3. Social media icons that redirect to the pages ─────────────────────
    def test_every_social_link_points_at_a_real_profile(self):
        for url in ('https://www.linkedin.com/company/social-lightbw',
                    'https://x.com/SocialLightBW',
                    'https://www.facebook.com/SocialLightBW',
                    'https://www.instagram.com/sociallight.africa'):
            self.assertContains(self.landing, url)

    def test_social_links_render_an_icon_and_are_labelled(self):
        """DPO asked for icons, not text. Screen readers still need the name,
        hence aria-label rather than an icon alone."""
        social = re.search(r'class="social".*?</div>\s*</div>', self.html, re.S)
        self.assertIsNotNone(social, 'No social block found in the footer.')
        block = social.group(0)
        self.assertEqual(block.count('<svg'), 4)
        for name in ('LinkedIn', 'X', 'Facebook', 'Instagram'):
            self.assertIn(f'aria-label="Social Light on {name}"', block)

    # ── 4. Products with descriptions and prices ─────────────────────────────
    def test_pricing_page_is_public_and_lists_every_package_with_a_price(self):
        response = self.client.get(reverse('monitor:pricing'))
        self.assertEqual(response.status_code, 200)

        packages = Package.objects.filter(is_active=True, is_public=True)
        self.assertTrue(packages.exists(), 'No public packages to advertise.')
        for package in packages:
            self.assertContains(response, package.name)
            # Either a figure or an explicit override such as "Custom" — a card
            # showing neither is a product with no price on it.
            self.assertTrue(
                package.price or package.price_override,
                f'{package.name} has neither a price nor a price_override.')
            self.assertTrue(
                package.features or package.tagline,
                f'{package.name} has no description for a customer to read.')

    def test_pricing_is_reachable_from_the_landing_page(self):
        self.assertContains(self.landing, reverse('monitor:pricing'))

    # ── 5. About us ──────────────────────────────────────────────────────────
    def test_about_section_exists_and_is_linked(self):
        self.assertContains(self.landing, 'id="about"')
        self.assertContains(self.landing, 'href="#about"')

    # ── 6. Onboarding process ────────────────────────────────────────────────
    def test_onboarding_process_is_published_and_linked(self):
        self.assertContains(self.landing, 'id="getting-started"')
        self.assertContains(self.landing, 'href="#getting-started"')

    def test_onboarding_process_lists_the_steps_in_order(self):
        section = self.html.split('id="getting-started"', 1)[1].split('</section>', 1)[0]
        numbers = re.findall(r'<span class="num">(\d+)</span>', section)
        self.assertEqual(numbers, ['1', '2', '3', '4', '5', '6'])

    def test_published_process_matches_the_wizard_it_describes(self):
        """The section is hand-written prose, so it can drift from the real
        wizard. This does not compare wording — it fails if the number of steps a
        user actually walks through changes, which is the moment to re-read it."""
        from monitor import onboarding

        # Everything except the terminal "You're all set" screen.
        real_steps = [s for s in onboarding.STEPS if not s.is_terminal]
        self.assertEqual(
            len(real_steps), 8,
            'The onboarding wizard changed. Re-read the "Getting started" section '
            'in landing.html and update it to match before changing this number.')

    def test_getting_started_links_all_resolve(self):
        """Every link in the new section must lead somewhere real."""
        section = self.html.split('id="getting-started"', 1)[1].split('</section>', 1)[0]
        hrefs = re.findall(r'href="([^"]+)"', section)
        self.assertTrue(hrefs, 'No links found in the getting-started section.')
        for href in hrefs:
            if href.startswith(('mailto:', '#')):
                continue
            with self.subTest(href=href):
                self.assertEqual(self.client.get(href).status_code, 200)

    # ── Whole-footer sweep ───────────────────────────────────────────────────
    def test_no_placeholder_anchors_anywhere_on_the_landing_page(self):
        """A placeholder such as href="#privacy" pointing at no such element is a
        dead end, and has been reported as a bug on this page before."""
        ids = set(re.findall(r'id="([^"]+)"', self.html))
        fragments = {h[1:] for h in re.findall(r'href="(#[^"]+)"', self.html) if len(h) > 1}
        self.assertEqual(fragments - ids, set(),
                         'These href="#..." links point at no element on the page.')

    def test_every_internal_footer_link_resolves(self):
        footer = self.html.split('<footer', 1)[1]
        for href in re.findall(r'href="(/[^"]*)"', footer):
            with self.subTest(href=href):
                self.assertEqual(self.client.get(href).status_code, 200)
