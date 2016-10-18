# encoding: utf-8
from StringIO import StringIO
import datetime
import os
import sys
import site
import re
import tempfile

from nose.tools import (
    assert_raises,
    assert_raises_regexp,
    assert_not_equal,
    eq_,
    set_trace,
)

from psycopg2.extras import NumericRange
from sqlalchemy.orm.exc import (
    NoResultFound,
)

from config import (
    Configuration, 
    temp_config,
)

from model import (
    Annotation,
    BaseCoverageRecord,
    CirculationEvent,
    Classification,
    Collection,
    Complaint,
    Contributor,
    CoverageRecord,
    Credential,
    CustomListEntry,
    DataSource,
    DeliveryMechanism,
    Genre,
    Hold,
    Hyperlink,
    LicensePool,
    Measurement,
    Patron,
    Representation,
    Resource,
    RightsStatus,
    SessionManager,
    Subject,
    Timestamp,
    Work,
    WorkCoverageRecord,
    WorkGenre,
    Identifier,
    Edition,
    create,
    get_one,
    get_one_or_create,
)
from external_search import (
    DummyExternalSearchIndex,
)

import classifier
from classifier import (
    Classifier,
    Fantasy,
    Romance,
    Science_Fiction,
    Drama,
)

from . import (
    DatabaseTest,
    DummyHTTPClient,
)

from analytics import (
    Analytics,
    temp_analytics
)
from mock_analytics_provider import MockAnalyticsProvider

class TestDataSource(DatabaseTest):

    def test_lookup(self):
        gutenberg = DataSource.lookup(self._db, DataSource.GUTENBERG)
        eq_(DataSource.GUTENBERG, gutenberg.name)
        eq_(True, gutenberg.offers_licenses)

    def test_lookup_by_deprecated_name(self):
        threem = DataSource.lookup(self._db, "3M")
        eq_(DataSource.BIBLIOTHECA, threem.name)
        assert DataSource.BIBLIOTHECA != "3M"
        
    def test_lookup_returns_none_for_nonexistent_source(self):
        eq_(None, DataSource.lookup(
            self._db, "No such data source " + self._str))

    def test_metadata_sources_for(self):
        content_cafe = DataSource.lookup(self._db, DataSource.CONTENT_CAFE)
        isbn_metadata_sources = DataSource.metadata_sources_for(
            self._db, Identifier.ISBN
        )

        eq_(1, len(isbn_metadata_sources))
        eq_([content_cafe], isbn_metadata_sources)

    def test_license_source_for(self):
        identifier = self._identifier(Identifier.OVERDRIVE_ID)
        source = DataSource.license_source_for(self._db, identifier)
        eq_(DataSource.OVERDRIVE, source.name)

    def test_license_source_for_string(self):
        source = DataSource.license_source_for(
            self._db, Identifier.THREEM_ID)
        eq_(DataSource.THREEM, source.name)

    def test_license_source_fails_if_identifier_type_does_not_provide_licenses(self):
        identifier = self._identifier(DataSource.MANUAL)
        assert_raises(
            NoResultFound, DataSource.license_source_for, self._db, identifier)
            

class TestIdentifier(DatabaseTest):

    def test_for_foreign_id(self):
        identifier_type = Identifier.ISBN
        isbn = "3293000061"

        # Getting the data automatically creates a database record.
        identifier, was_new = Identifier.for_foreign_id(
            self._db, identifier_type, isbn)
        eq_(Identifier.ISBN, identifier.type)
        eq_(isbn, identifier.identifier)
        eq_(True, was_new)

        # If we get it again we get the same data, but it's no longer new.
        identifier2, was_new = Identifier.for_foreign_id(
            self._db, identifier_type, isbn)
        eq_(identifier, identifier2)
        eq_(False, was_new)

        # If we pass in no data we get nothing back.
        eq_(None, Identifier.for_foreign_id(self._db, None, None))

    def test_for_foreign_id_by_deprecated_type(self):
        threem_id, is_new = Identifier.for_foreign_id(
            self._db, "3M ID", self._str
        )
        eq_(Identifier.BIBLIOTHECA_ID, threem_id.type)
        assert Identifier.BIBLIOTHECA_ID != "3M ID"
        
    def test_for_foreign_id_without_autocreate(self):
        identifier_type = Identifier.ISBN
        isbn = self._str

        # We don't want to auto-create a database record, so we set
        # autocreate=False
        identifier, was_new = Identifier.for_foreign_id(
            self._db, identifier_type, isbn, autocreate=False)
        eq_(None, identifier)
        eq_(False, was_new)

    def test_from_asin(self):
        isbn10 = '1449358063'
        isbn13 = '9781449358068'
        asin = 'B0088IYM3C'
        isbn13_with_dashes = '978-144-935-8068'        

        i_isbn10, new1 = Identifier.from_asin(self._db, isbn10)
        i_isbn13, new2 = Identifier.from_asin(self._db, isbn13)
        i_asin, new3 = Identifier.from_asin(self._db, asin)
        i_isbn13_2, new4 = Identifier.from_asin(self._db, isbn13_with_dashes)

        # The three ISBNs are equivalent, so they got turned into the same
        # Identifier, using the ISBN13.
        eq_(i_isbn10, i_isbn13)
        eq_(i_isbn13_2, i_isbn13)
        eq_(Identifier.ISBN, i_isbn10.type)
        eq_(isbn13, i_isbn10.identifier)
        eq_(True, new1)
        eq_(False, new2)
        eq_(False, new4)

        eq_(Identifier.ASIN, i_asin.type)
        eq_(asin, i_asin.identifier)

    def test_urn(self):
        # ISBN identifiers use the ISBN URN scheme.
        identifier, ignore = Identifier.for_foreign_id(
            self._db, Identifier.ISBN, "9781449358068")
        eq_("urn:isbn:9781449358068", identifier.urn)

        # URI identifiers don't need a URN scheme.
        identifier, ignore = Identifier.for_foreign_id(
            self._db, Identifier.URI, "http://example.com/")
        eq_(identifier.identifier, identifier.urn)

        # Gutenberg identifiers use Gutenberg's URL-based sceheme
        identifier = self._identifier(Identifier.GUTENBERG_ID)
        eq_(Identifier.GUTENBERG_URN_SCHEME_PREFIX + identifier.identifier,
            identifier.urn)

        # All other identifiers use our custom URN scheme.
        identifier = self._identifier(Identifier.OVERDRIVE_ID)
        assert identifier.urn.startswith(Identifier.URN_SCHEME_PREFIX)

    def test_parse_urn(self):

        # We can parse our custom URNs back into identifiers.
        identifier = self._identifier()
        self._db.commit()
        new_identifier, ignore = Identifier.parse_urn(self._db, identifier.urn)
        eq_(identifier, new_identifier)

        # We can parse urn:isbn URNs into ISBN identifiers. ISBN-10s are
        # converted to ISBN-13s.
        identifier, ignore = Identifier.for_foreign_id(
            self._db, Identifier.ISBN, "9781449358068")
        isbn_urn = "urn:isbn:1449358063"
        isbn_identifier, ignore = Identifier.parse_urn(self._db, isbn_urn)
        eq_(Identifier.ISBN, isbn_identifier.type)
        eq_("9781449358068", isbn_identifier.identifier)

        isbn_urn = "urn:isbn:9781449358068"
        isbn_identifier2, ignore = Identifier.parse_urn(self._db, isbn_urn)
        eq_(isbn_identifier2, isbn_identifier)

        # We can parse ordinary http: or https: URLs into URI
        # identifiers.
        http_identifier, ignore = Identifier.parse_urn(
            self._db, "http://example.com")
        eq_(Identifier.URI, http_identifier.type)
        eq_("http://example.com", http_identifier.identifier)

        https_identifier, ignore = Identifier.parse_urn(
            self._db, "https://example.com")
        eq_(Identifier.URI, https_identifier.type)
        eq_("https://example.com", https_identifier.identifier)

        # A URN we can't handle raises an exception.
        ftp_urn = "ftp://example.com"
        assert_raises(ValueError, Identifier.parse_urn, self._db, ftp_urn)

        # An invalid ISBN raises an exception.
        assert_raises(ValueError, Identifier.parse_urn, self._db, "urn:isbn:notanisbn")

        # Pass in None and you get None.
        eq_(None, Identifier.parse_urn(self._db, None))
        
    def parse_urn_must_support_license_pools(self):
        # We have no way of associating ISBNs with license pools.
        # If we try to parse an ISBN URN in a context that only accepts
        # URNs that can have associated license pools, we get an exception.
        isbn_urn = "urn:isbn:1449358063"
        assert_raises(
            Identifier.UnresolvableIdentifierException, 
            Identifier.parse_urn, self._db, isbn_urn, 
            must_support_license_pools=True)

    def test_recursively_equivalent_identifier_ids(self):
        identifier = self._identifier()
        data_source = DataSource.lookup(self._db, DataSource.MANUAL)

        strong_equivalent = self._identifier()
        identifier.equivalent_to(data_source, strong_equivalent, 0.9)

        weak_equivalent = self._identifier()
        identifier.equivalent_to(data_source, weak_equivalent, 0.2)

        level_2_equivalent = self._identifier()
        strong_equivalent.equivalent_to(data_source, level_2_equivalent, 0.5)

        level_3_equivalent = self._identifier()
        level_2_equivalent.equivalent_to(data_source, level_3_equivalent, 0.9)

        level_4_equivalent = self._identifier()
        level_3_equivalent.equivalent_to(data_source, level_4_equivalent, 0.6)

        unrelated = self._identifier()

        # With a low threshold and enough levels, we find all the identifiers.
        equivs = Identifier.recursively_equivalent_identifier_ids(
            self._db, [identifier.id], levels=5, threshold=0.1)
        eq_(set([identifier.id,
                 strong_equivalent.id,
                 weak_equivalent.id,
                 level_2_equivalent.id,
                 level_3_equivalent.id,
                 level_4_equivalent.id]),
            set(equivs[identifier.id]))

        # If we only look at one level, we don't find the level 2, 3, or 4 identifiers.
        equivs = Identifier.recursively_equivalent_identifier_ids(
            self._db, [identifier.id], levels=1, threshold=0.1)
        eq_(set([identifier.id,
                 strong_equivalent.id,
                 weak_equivalent.id]),
            set(equivs[identifier.id]))

        # If we raise the threshold, we don't find the weak identifier.
        equivs = Identifier.recursively_equivalent_identifier_ids(
            self._db, [identifier.id], levels=1, threshold=0.4)
        eq_(set([identifier.id,
                 strong_equivalent.id]),
            set(equivs[identifier.id]))

        # For deeper levels, the strength is the product of the strengths
        # of all the equivalencies in between the two identifiers.

        # In this example:
        # identifier - level_2_equivalent = 0.9 * 0.5 = 0.45
        # identifier - level_3_equivalent = 0.9 * 0.5 * 0.9 = 0.405
        # identifier - level_4_equivalent = 0.9 * 0.5 * 0.9 * 0.6 = 0.243

        # With a threshold of 0.5, level 2 and all subsequent levels are too weak.
        equivs = Identifier.recursively_equivalent_identifier_ids(
            self._db, [identifier.id], levels=5, threshold=0.5)
        eq_(set([identifier.id,
                 strong_equivalent.id]),
            set(equivs[identifier.id]))

        # With a threshold of 0.25, level 2 is strong enough, but level
        # 4 is too weak.
        equivs = Identifier.recursively_equivalent_identifier_ids(
            self._db, [identifier.id], levels=5, threshold=0.25)
        eq_(set([identifier.id,
                 strong_equivalent.id,
                 level_2_equivalent.id,
                 level_3_equivalent.id]),
            set(equivs[identifier.id]))

        # It also works if we start from other identifiers.
        equivs = Identifier.recursively_equivalent_identifier_ids(
            self._db, [strong_equivalent.id], levels=5, threshold=0.1)
        eq_(set([identifier.id,
                 strong_equivalent.id,
                 weak_equivalent.id,
                 level_2_equivalent.id,
                 level_3_equivalent.id,
                 level_4_equivalent.id]),
            set(equivs[strong_equivalent.id]))

        equivs = Identifier.recursively_equivalent_identifier_ids(
            self._db, [level_4_equivalent.id], levels=5, threshold=0.1)
        eq_(set([identifier.id,
                 strong_equivalent.id,
                 level_2_equivalent.id,
                 level_3_equivalent.id,
                 level_4_equivalent.id]),
            set(equivs[level_4_equivalent.id]))
        
        equivs = Identifier.recursively_equivalent_identifier_ids(
            self._db, [level_4_equivalent.id], levels=5, threshold=0.5)
        eq_(set([level_2_equivalent.id,
                 level_3_equivalent.id,
                 level_4_equivalent.id]),
            set(equivs[level_4_equivalent.id]))
        
        # A chain of very strong equivalents can keep a high strength
        # even at deep levels. This wouldn't work if we changed the strength
        # threshold by level instead of accumulating a strength product.
        another_identifier = self._identifier()
        l2 = self._identifier()
        l3 = self._identifier()
        l4 = self._identifier()
        l2.equivalent_to(data_source, another_identifier, 1)
        l3.equivalent_to(data_source, l2, 1)
        l4.equivalent_to(data_source, l3, 0.9)
        equivs = Identifier.recursively_equivalent_identifier_ids(
            self._db, [another_identifier.id], levels=5, threshold=0.89)
        eq_(set([another_identifier.id,
                 l2.id,
                 l3.id,
                 l4.id]),
            set(equivs[another_identifier.id]))

        # We can look for multiple identifiers at once.
        equivs = Identifier.recursively_equivalent_identifier_ids(
            self._db, [identifier.id, level_3_equivalent.id], levels=2, threshold=0.8)
        eq_(set([identifier.id,
                 strong_equivalent.id]),
            set(equivs[identifier.id]))
        eq_(set([level_2_equivalent.id,
                 level_3_equivalent.id]),
            set(equivs[level_3_equivalent.id]))

        # The query uses the same db function, but returns equivalents
        # for all identifiers together so it can be used as a subquery.
        query = Identifier.recursively_equivalent_identifier_ids_query(
            Identifier.id, levels=5, threshold=0.1)
        query = query.where(Identifier.id==identifier.id)
        results = self._db.execute(query)
        equivalent_ids = [r[0] for r in results]
        eq_(set([identifier.id,
                 strong_equivalent.id,
                 weak_equivalent.id,
                 level_2_equivalent.id,
                 level_3_equivalent.id,
                 level_4_equivalent.id]),
            set(equivalent_ids))

        query = Identifier.recursively_equivalent_identifier_ids_query(
            Identifier.id, levels=2, threshold=0.8)
        query = query.where(Identifier.id.in_([identifier.id, level_3_equivalent.id]))
        results = self._db.execute(query)
        equivalent_ids = [r[0] for r in results]
        eq_(set([identifier.id,
                 strong_equivalent.id,
                 level_2_equivalent.id,
                 level_3_equivalent.id]),
            set(equivalent_ids))


    def test_missing_coverage_from(self):
        gutenberg = DataSource.lookup(self._db, DataSource.GUTENBERG)
        oclc = DataSource.lookup(self._db, DataSource.OCLC)
        web = DataSource.lookup(self._db, DataSource.WEB)

        # Here are two Gutenberg records.
        g1, ignore = Edition.for_foreign_id(
            self._db, gutenberg, Identifier.GUTENBERG_ID, "1")

        g2, ignore = Edition.for_foreign_id(
            self._db, gutenberg, Identifier.GUTENBERG_ID, "2")

        # One of them has coverage from OCLC Classify
        c1 = self._coverage_record(g1, oclc)

        # The other has coverage from a specific operation on OCLC Classify
        c2 = self._coverage_record(g2, oclc, "some operation")

        # Here's a web record, just sitting there.
        w, ignore = Edition.for_foreign_id(
            self._db, web, Identifier.URI, "http://www.foo.com/")

        # If we run missing_coverage_from we pick up the Gutenberg
        # record with no generic OCLC coverage. It doesn't pick up the
        # other Gutenberg record, it doesn't pick up the web record,
        # and it doesn't pick up the OCLC coverage for a specific
        # operation.
        [in_gutenberg_but_not_in_oclc] = Identifier.missing_coverage_from(
            self._db, [Identifier.GUTENBERG_ID], oclc).all()

        eq_(g2.primary_identifier, in_gutenberg_but_not_in_oclc)

        # If we ask about a specific operation, we get the Gutenberg
        # record that has coverage for that operation, but not the one
        # that has generic OCLC coverage.

        [has_generic_coverage_only] = Identifier.missing_coverage_from(
            self._db, [Identifier.GUTENBERG_ID], oclc, "some operation").all()
        eq_(g1.primary_identifier, has_generic_coverage_only)

        # We don't put web sites into OCLC, so this will pick up the
        # web record (but not the Gutenberg record).
        [in_web_but_not_in_oclc] = Identifier.missing_coverage_from(
            self._db, [Identifier.URI], oclc).all()
        eq_(w.primary_identifier, in_web_but_not_in_oclc)

        # We don't use the web as a source of coverage, so this will
        # return both Gutenberg records (but not the web record).
        eq_([g1.primary_identifier.id, g2.primary_identifier.id], sorted(
            [x.id for x in Identifier.missing_coverage_from(
                self._db, [Identifier.GUTENBERG_ID], web)])
        )

    def test_missing_coverage_from_with_cutoff_date(self):
        gutenberg = DataSource.lookup(self._db, DataSource.GUTENBERG)
        oclc = DataSource.lookup(self._db, DataSource.OCLC)
        web = DataSource.lookup(self._db, DataSource.WEB)

        # Here's an Edition with a coverage record from OCLC classify.
        gutenberg, ignore = Edition.for_foreign_id(
            self._db, gutenberg, Identifier.GUTENBERG_ID, "1")
        identifier = gutenberg.primary_identifier
        oclc = DataSource.lookup(self._db, DataSource.OCLC)
        coverage = self._coverage_record(gutenberg, oclc)

        # The CoverageRecord knows when the coverage was provided.
        timestamp = coverage.timestamp
        
        # If we ask for Identifiers that are missing coverage records
        # as of that time, we see nothing.
        eq_(
            [], 
            Identifier.missing_coverage_from(
                self._db, [identifier.type], oclc, 
                count_as_missing_before=timestamp
            ).all()
        )

        # But if we give a time one second later, the Identifier is
        # missing coverage.
        eq_(
            [identifier], 
            Identifier.missing_coverage_from(
                self._db, [identifier.type], oclc, 
                count_as_missing_before=timestamp+datetime.timedelta(seconds=1)
            ).all()
        )


class TestSubject(DatabaseTest):

    def test_lookup_autocreate(self):
        # By default, Subject.lookup creates a Subject that doesn't exist.
        identifier = self._str
        subject, was_new = Subject.lookup(
            self._db, Subject.TAG, identifier, None
        )
        eq_(True, was_new)
        eq_(identifier, subject.identifier)

        # But you can tell it not to autocreate.
        identifier2 = self._str
        subject, was_new = Subject.lookup(
            self._db, Subject.TAG, identifier2, None, autocreate=False
        )
        eq_(False, was_new)
        eq_(None, subject)
        
    def test_assign_to_genre_can_remove_genre(self):
        # Here's a Subject that identifies children's books.
        subject, was_new = Subject.lookup(self._db, Subject.TAG, "Children's books", None)

        # The genre and audience data for this Subject is totally wrong.
        subject.audience = Classifier.AUDIENCE_ADULT
        subject.target_age = NumericRange(1,10)
        subject.fiction = False
        sf, ignore = Genre.lookup(self._db, "Science Fiction")
        subject.genre = sf

        # But calling assign_to_genre() will fix it.
        subject.assign_to_genre()
        eq_(Classifier.AUDIENCE_CHILDREN, subject.audience)
        eq_(NumericRange(None, None, '[]'), subject.target_age)
        eq_(None, subject.genre)
        eq_(None, subject.fiction)


class TestContributor(DatabaseTest):

    def test_lookup_by_viaf(self):

        # Two contributors named Bob.
        bob1, new = Contributor.lookup(self._db, sort_name="Bob", viaf="foo")
        bob2, new = Contributor.lookup(self._db, sort_name="Bob", viaf="bar")

        assert bob1 != bob2

        eq_((bob1, False), Contributor.lookup(self._db, viaf="foo"))

    def test_lookup_by_lc(self):

        # Two contributors named Bob.
        bob1, new = Contributor.lookup(self._db, sort_name="Bob", lc="foo")
        bob2, new = Contributor.lookup(self._db, sort_name="Bob", lc="bar")

        assert bob1 != bob2

        eq_((bob1, False), Contributor.lookup(self._db, lc="foo"))

    def test_lookup_by_name(self):

        # Two contributors named Bob.
        bob1, new = Contributor.lookup(self._db, sort_name=u"Bob", lc=u"foo")
        bob2, new = Contributor.lookup(self._db, sort_name=u"Bob", lc=u"bar")

        # Lookup by name finds both of them.
        bobs, new = Contributor.lookup(self._db, sort_name=u"Bob")
        eq_(False, new)
        eq_(["Bob", "Bob"], [x.sort_name for x in bobs])

    def test_create_by_lookup(self):
        [bob1], new = Contributor.lookup(self._db, sort_name=u"Bob")
        eq_("Bob", bob1.sort_name)
        eq_(True, new)

        [bob2], new = Contributor.lookup(self._db, sort_name=u"Bob")
        eq_(bob1, bob2)
        eq_(False, new)

    def test_merge(self):

        # Here's Robert.
        [robert], ignore = Contributor.lookup(self._db, sort_name=u"Robert")
        
        # Here's Bob.
        [bob], ignore = Contributor.lookup(self._db, sort_name=u"Bob")
        bob.extra[u'foo'] = u'bar'
        bob.aliases = [u'Bobby']
        bob.viaf = u'viaf'
        bob.lc = u'lc'
        bob.display_name = u"Bob's display name"
        bob.family_name = u"Bobb"
        bob.wikipedia_name = u"Bob_(Person)"

        # Each is a contributor to a Edition.
        data_source = DataSource.lookup(self._db, DataSource.GUTENBERG)

        roberts_book, ignore = Edition.for_foreign_id(
            self._db, data_source, Identifier.GUTENBERG_ID, u"1")
        roberts_book.add_contributor(robert, Contributor.AUTHOR_ROLE)

        bobs_book, ignore = Edition.for_foreign_id(
            self._db, data_source, Identifier.GUTENBERG_ID, u"10")
        bobs_book.add_contributor(bob, Contributor.AUTHOR_ROLE)

        # In a shocking turn of events, it transpires that "Bob" and
        # "Robert" are the same person. We merge "Bob" into Robert
        # thusly:
        bob.merge_into(robert)

        # 'Bob' is now listed as an alias for Robert, as is Bob's
        # alias.
        eq_([u'Bob', u'Bobby'], robert.aliases)

        # The extra information associated with Bob is now associated
        # with Robert.
        eq_(u'bar', robert.extra['foo'])

        eq_(u"viaf", robert.viaf)
        eq_(u"lc", robert.lc)
        eq_(u"Bobb", robert.family_name)
        eq_(u"Bob's display name", robert.display_name)
        eq_(u"Bob_(Person)", robert.wikipedia_name)

        # The standalone 'Bob' record has been removed from the database.
        eq_(
            [], 
            self._db.query(Contributor).filter(Contributor.sort_name=="Bob").all())

        # Bob's book is now associated with 'Robert', not the standalone
        # 'Bob' record.
        eq_([robert], bobs_book.author_contributors)

    def _names(self, in_name, out_family, out_display,
               default_display_name=None):
        f, d = Contributor._default_names(in_name, default_display_name)
        eq_(f, out_family)
        eq_(d, out_display)

    def test_default_names(self):

        # Pass in a default display name and it will always be used.
        self._names("Jones, Bob", "Jones", "Sally Smith",
                    default_display_name="Sally Smith")

        # Corporate names are untouched and get no family name.
        self._names("Bob's Books.", None, "Bob's Books.")
        self._names("Bob's Books, Inc.", None, "Bob's Books, Inc.")
        self._names("Little, Brown &amp; Co.", None, "Little, Brown & Co.")
        self._names("Philadelphia Broad Street Church (Philadelphia, Pa.)",
                    None, "Philadelphia Broad Street Church")

        # Dates and other gibberish after a name is removed.
        self._names("Twain, Mark, 1855-1910", "Twain", "Mark Twain")
        self._names("Twain, Mark, ???-1910", "Twain", "Mark Twain")
        self._names("Twain, Mark, circ. 1900", "Twain", "Mark Twain")
        self._names("Twain, Mark, !@#!@", "Twain", "Mark Twain")
        self._names(
            "Coolbrith, Ina D. 1842?-1928", "Coolbrith", "Ina D. Coolbrith")
        self._names("Caesar, Julius, 1st cent.", "Caesar", "Julius Caesar")
        self._names("Arrian, 2nd cent.", "Arrian", "Arrian")
        self._names("Hafiz, 14th cent.", "Hafiz", "Hafiz")
        self._names("Hormel, Bob 1950?-", "Hormel", "Bob Hormel")
        self._names("Holland, Henry 1583-1650? Monumenta sepulchraria Sancti Pauli",
                    "Holland", "Henry Holland")
        

        # Suffixes stay on the end, except for "Mrs.", which goes
        # to the front.
        self._names("Twain, Mark, Jr.", "Twain", "Mark Twain, Jr.")
        self._names("House, Gregory, M.D.", "House", "Gregory House, M.D.")
        self._names("Twain, Mark, Mrs.", "Twain", "Mrs. Mark Twain")
        self._names("Twain, Mark, Mrs", "Twain", "Mrs Mark Twain")

        # The easy case.
        self._names("Twain, Mark", "Twain", "Mark Twain")
        self._names("Geering, R. G.", "Geering", "R. G. Geering")

class TestEdition(DatabaseTest):

    def test_author_contributors(self):
        data_source = DataSource.lookup(self._db, DataSource.GUTENBERG)
        id = self._str
        type = Identifier.GUTENBERG_ID

        edition, was_new = Edition.for_foreign_id(
            self._db, data_source, type, id
        )

        # We've listed the same person as primary author and author.
        [alice], ignore = Contributor.lookup(self._db, "Adder, Alice")
        edition.add_contributor(
            alice, [Contributor.AUTHOR_ROLE, Contributor.PRIMARY_AUTHOR_ROLE]
        )

        # We've listed a different person as illustrator.
        [bob], ignore = Contributor.lookup(self._db, "Bitshifter, Bob")
        edition.add_contributor(bob, [Contributor.ILLUSTRATOR_ROLE])

        # Both contributors show up in .contributors.
        eq_(set([alice, bob]), edition.contributors)

        # Only the author shows up in .author_contributors, and she
        # only shows up once.
        eq_([alice], edition.author_contributors)

    def test_for_foreign_id(self):
        """Verify we can get a data source's view of a foreign id."""
        data_source = DataSource.lookup(self._db, DataSource.GUTENBERG)
        id = "549"
        type = Identifier.GUTENBERG_ID

        record, was_new = Edition.for_foreign_id(
            self._db, data_source, type, id)
        eq_(data_source, record.data_source)
        identifier = record.primary_identifier
        eq_(id, identifier.identifier)
        eq_(type, identifier.type)
        eq_(True, was_new)
        eq_([identifier], record.equivalent_identifiers())

        # We can get the same work record by providing only the name
        # of the data source.
        record, was_new = Edition.for_foreign_id(
            self._db, DataSource.GUTENBERG, type, id)
        eq_(data_source, record.data_source)
        eq_(identifier, record.primary_identifier)
        eq_(False, was_new)

    def test_missing_coverage_from(self):
        gutenberg = DataSource.lookup(self._db, DataSource.GUTENBERG)
        oclc = DataSource.lookup(self._db, DataSource.OCLC)
        web = DataSource.lookup(self._db, DataSource.WEB)

        # Here are two Gutenberg records.
        g1, ignore = Edition.for_foreign_id(
            self._db, gutenberg, Identifier.GUTENBERG_ID, "1")

        g2, ignore = Edition.for_foreign_id(
            self._db, gutenberg, Identifier.GUTENBERG_ID, "2")

        # One of them has coverage from OCLC Classify
        c1 = self._coverage_record(g1, oclc)

        # The other has coverage from a specific operation on OCLC Classify
        c2 = self._coverage_record(g2, oclc, "some operation")

        # Here's a web record, just sitting there.
        w, ignore = Edition.for_foreign_id(
            self._db, web, Identifier.URI, "http://www.foo.com/")

        # missing_coverage_from picks up the Gutenberg record with no
        # coverage from OCLC. It doesn't pick up the other
        # Gutenberg record, and it doesn't pick up the web record.
        [in_gutenberg_but_not_in_oclc] = Edition.missing_coverage_from(
            self._db, gutenberg, oclc).all()

        eq_(g2, in_gutenberg_but_not_in_oclc)

        # If we ask about a specific operation, we get the Gutenberg
        # record that has coverage for that operation, but not the one
        # that has generic OCLC coverage.
        [has_generic_coverage_only] = Edition.missing_coverage_from(
            self._db, gutenberg, oclc, "some operation").all()
        eq_(g1, has_generic_coverage_only)

        # We don't put web sites into OCLC, so this will pick up the
        # web record (but not the Gutenberg record).
        [in_web_but_not_in_oclc] = Edition.missing_coverage_from(
            self._db, web, oclc).all()
        eq_(w, in_web_but_not_in_oclc)

        # We don't use the web as a source of coverage, so this will
        # return both Gutenberg records (but not the web record).
        eq_([g1.id, g2.id], sorted([x.id for x in Edition.missing_coverage_from(
            self._db, gutenberg, web)]))

    def test_equivalent_identifiers(self):

        edition = self._edition()
        identifier = self._identifier()
        data_source = DataSource.lookup(self._db, DataSource.OCLC)

        identifier.equivalent_to(data_source, edition.primary_identifier, 0.6)

        eq_(set([identifier, edition.primary_identifier]),
            set(edition.equivalent_identifiers(threshold=0.5)))

        eq_(set([edition.primary_identifier]),
            set(edition.equivalent_identifiers(threshold=0.7)))

    def test_recursive_edition_equivalence(self):

        # Here's a Edition for a Project Gutenberg text.
        gutenberg, gutenberg_pool = self._edition(
            data_source_name=DataSource.GUTENBERG,
            identifier_type=Identifier.GUTENBERG_ID,
            identifier_id="1",
            with_open_access_download=True,
            title="Original Gutenberg text")

        # Here's a Edition for an Open Library text.
        open_library, open_library_pool = self._edition(
            data_source_name=DataSource.OPEN_LIBRARY,
            identifier_type=Identifier.OPEN_LIBRARY_ID,
            identifier_id="W1111",
            with_open_access_download=True,
            title="Open Library record")

        # We've learned from OCLC Classify that the Gutenberg text is
        # equivalent to a certain OCLC Number. We've learned from OCLC
        # Linked Data that the Open Library text is equivalent to the
        # same OCLC Number.
        oclc_classify = DataSource.lookup(self._db, DataSource.OCLC)
        oclc_linked_data = DataSource.lookup(self._db, DataSource.OCLC_LINKED_DATA)

        oclc_number, ignore = Identifier.for_foreign_id(
            self._db, Identifier.OCLC_NUMBER, "22")
        gutenberg.primary_identifier.equivalent_to(
            oclc_classify, oclc_number, 1)
        open_library.primary_identifier.equivalent_to(
            oclc_linked_data, oclc_number, 1)
       
        # Here's a Edition for a Recovering the Classics cover.
        web_source = DataSource.lookup(self._db, DataSource.WEB)
        recovering, ignore = Edition.for_foreign_id(
            self._db, web_source, Identifier.URI, 
            "http://recoveringtheclassics.com/pride-and-prejudice.jpg")
        recovering.title = "Recovering the Classics cover"

        # We've manually associated that Edition's URI directly
        # with the Project Gutenberg text.
        manual = DataSource.lookup(self._db, DataSource.MANUAL)
        gutenberg.primary_identifier.equivalent_to(
            manual, recovering.primary_identifier, 1)

        # Finally, here's a completely unrelated Edition, which
        # will not be showing up.
        gutenberg2, gutenberg2_pool = self._edition(
            data_source_name=DataSource.GUTENBERG,
            identifier_type=Identifier.GUTENBERG_ID,
            identifier_id="2",
            with_open_access_download=True,
            title="Unrelated Gutenberg record.")

        # When we call equivalent_editions on the Project Gutenberg
        # Edition, we get three Editions: the Gutenberg record
        # itself, the Open Library record, and the Recovering the
        # Classics record.
        #
        # We get the Open Library record because it's associated with
        # the same OCLC Number as the Gutenberg record. We get the
        # Recovering the Classics record because it's associated
        # directly with the Gutenberg record.
        results = list(gutenberg.equivalent_editions())
        eq_(3, len(results))
        assert gutenberg in results
        assert open_library in results
        assert recovering in results

        # Here's a Work that incorporates one of the Gutenberg records.
        work = self._work()
        work.license_pools.extend([gutenberg2_pool])

        # Its set-of-all-editions contains only one record.
        eq_(1, work.all_editions().count())

        # If we add the other Gutenberg record to it, then its
        # set-of-all-editions is extended by that record, *plus*
        # all the Editions equivalent to that record.
        work.license_pools.extend([gutenberg_pool])
        eq_(4, work.all_editions().count())

    def test_calculate_presentation_title(self):
        wr = self._edition(title="The Foo")
        wr.calculate_presentation()
        eq_("Foo, The", wr.sort_title)

        wr = self._edition(title="A Foo")
        wr.calculate_presentation()
        eq_("Foo, A", wr.sort_title)

    def test_calculate_presentation_missing_author(self):
        wr = self._edition()
        self._db.delete(wr.contributions[0])
        self._db.commit()
        wr.calculate_presentation()
        eq_(u"[Unknown]", wr.sort_author)
        eq_(u"[Unknown]", wr.author)

    def test_calculate_presentation_author(self):
        bob, ignore = self._contributor(sort_name="Bitshifter, Bob")
        wr = self._edition(authors=bob.sort_name)
        wr.calculate_presentation()
        eq_("Bob Bitshifter", wr.author)
        eq_("Bitshifter, Bob", wr.sort_author)

        bob.display_name="Bob A. Bitshifter"
        wr.calculate_presentation()
        eq_("Bob A. Bitshifter", wr.author)
        eq_("Bitshifter, Bob", wr.sort_author)

        kelly, ignore = self._contributor(sort_name="Accumulator, Kelly")
        wr.add_contributor(kelly, Contributor.AUTHOR_ROLE)
        wr.calculate_presentation()
        eq_("Kelly Accumulator, Bob A. Bitshifter", wr.author)
        eq_("Accumulator, Kelly ; Bitshifter, Bob", wr.sort_author)

    def test_set_summary(self):
        e, pool = self._edition(with_license_pool=True)
        work = self._work(presentation_edition=e)
        overdrive = DataSource.lookup(self._db, DataSource.OVERDRIVE)

        # Set the work's summmary.
        l1, new = pool.add_link(Hyperlink.DESCRIPTION, None, overdrive, "text/plain",
                      "F")
        work.set_summary(l1.resource)

        eq_(l1.resource, work.summary)
        eq_("F", work.summary_text)

        # Remove the summary.
        work.set_summary(None)
        
        eq_(None, work.summary)
        eq_("", work.summary_text)

    def test_calculate_evaluate_summary_quality_with_privileged_data_sources(self):
        e, pool = self._edition(with_license_pool=True)
        oclc = DataSource.lookup(self._db, DataSource.OCLC_LINKED_DATA)
        overdrive = DataSource.lookup(self._db, DataSource.OVERDRIVE)

        # There's a perfunctory description from Overdrive.
        l1, new = pool.add_link(Hyperlink.SHORT_DESCRIPTION, None, overdrive, "text/plain",
                      "F")

        overdrive_resource = l1.resource

        # There's a much better description from OCLC Linked Data.
        l2, new = pool.add_link(Hyperlink.DESCRIPTION, None, oclc, "text/plain",
                      """Nothing about working with his former high school crush, Stephanie Stephens, is ideal. Still, if Aaron Caruthers intends to save his grandmother's bakery, he must. Good thing he has a lot of ideas he can't wait to implement. He never imagines Stephanie would have her own ideas for the business. Or that they would clash with his!""")
        oclc_resource = l2.resource

        # In a head-to-head evaluation, the OCLC Linked Data description wins.
        ids = [e.primary_identifier.id]
        champ1, resources = Identifier.evaluate_summary_quality(self._db, ids)

        eq_(set([overdrive_resource, oclc_resource]), set(resources))
        eq_(oclc_resource, champ1)

        # But if we say that Overdrive is the privileged data source, it wins
        # automatically. The other resource isn't even considered.
        champ2, resources2 = Identifier.evaluate_summary_quality(
            self._db, ids, [overdrive])
        eq_(overdrive_resource, champ2)
        eq_([overdrive_resource], resources2)

        # If we say that some other data source is privileged, and
        # there are no descriptions from that data source, a
        # head-to-head evaluation is performed, and OCLC Linked Data
        # wins.
        threem = DataSource.lookup(self._db, DataSource.THREEM)
        champ3, resources3 = Identifier.evaluate_summary_quality(
            self._db, ids, [threem])
        eq_(set([overdrive_resource, oclc_resource]), set(resources3))
        eq_(oclc_resource, champ3)

        # If there are two privileged data sources and there's no
        # description from the first, the second is used.
        champ4, resources4 = Identifier.evaluate_summary_quality(
            self._db, ids, [threem, overdrive])
        eq_([overdrive_resource], resources4)
        eq_(overdrive_resource, champ4)

        # Even an empty string wins if it's from the most privileged data source.
        # This is not a silly example.  The librarian may choose to set the description 
        # to an empty string in the admin inteface, to override a bad overdrive/etc. description.
        staff = DataSource.lookup(self._db, DataSource.LIBRARY_STAFF)
        l3, new = pool.add_link(Hyperlink.SHORT_DESCRIPTION, None, staff, "text/plain", "")
        staff_resource = l3.resource

        champ5, resources5 = Identifier.evaluate_summary_quality(
            self._db, ids, [staff, overdrive])
        eq_([staff_resource], resources5)
        eq_(staff_resource, champ5)

    def test_calculate_presentation_cover(self):
        # TODO: Verify that a cover will be used even if it's some
        # distance away along the identifier-equivalence line.

        # TODO: Verify that a nearby cover takes precedence over a
        # faraway cover.
        pass

    def test_calculate_presentation_registers_coverage_records(self):
        edition = self._edition()
        identifier = edition.primary_identifier

        # This Identifier has no CoverageRecords.
        eq_([], identifier.coverage_records)

        # But once we calculate the Edition's presentation...
        edition.calculate_presentation()

        # Two CoverageRecords have been associated with this Identifier.
        records = identifier.coverage_records

        # One for setting the Edition metadata and one for choosing
        # the Edition's cover.
        expect = set([
            CoverageRecord.SET_EDITION_METADATA_OPERATION,
            CoverageRecord.CHOOSE_COVER_OPERATION]
        )
        eq_(expect, set([x.operation for x in records]))

        # We know the records are associated with this specific
        # Edition, not just the Identifier, because each
        # CoverageRecord's DataSource is set to this Edition's
        # DataSource.
        eq_(
            [edition.data_source, edition.data_source], 
            [x.data_source for x in records]
        )

    def test_no_permanent_work_id_for_edition_with_no_title(self):
        """An edition with no title is not assigned a permanent work ID."""
        edition = self._edition()
        edition.title = ''
        eq_(None, edition.permanent_work_id)
        edition.calculate_permanent_work_id()
        eq_(None, edition.permanent_work_id)
        edition.title = u'something'
        edition.calculate_permanent_work_id()
        assert_not_equal(None, edition.permanent_work_id)


class TestLicensePool(DatabaseTest):

    def test_for_foreign_id(self):
        """Verify we can get a LicensePool for a data source and an 
        appropriate work identifier."""
        now = datetime.datetime.utcnow()
        pool, was_new = LicensePool.for_foreign_id(
            self._db, DataSource.GUTENBERG, Identifier.GUTENBERG_ID, "541")
        assert (pool.availability_time - now).total_seconds() < 2
        eq_(True, was_new)
        eq_(DataSource.GUTENBERG, pool.data_source.name)
        eq_(Identifier.GUTENBERG_ID, pool.identifier.type)
        eq_("541", pool.identifier.identifier)        
        eq_(0, pool.licenses_owned)
        eq_(0, pool.licenses_available)
        eq_(0, pool.licenses_reserved)
        eq_(0, pool.patrons_in_hold_queue)

    def test_no_license_pool_for_data_source_that_offers_no_licenses(self):
        """OCLC doesn't offer licenses. It only provides metadata. We can get
        a Edition for OCLC's view of a book, but we cannot get a
        LicensePool for OCLC's view of a book.
        """
        assert_raises_regexp(
            ValueError, 
            'Data source "OCLC Classify" does not offer licenses',
            LicensePool.for_foreign_id,
            self._db, DataSource.OCLC, "1015", 
            Identifier.OCLC_WORK)

    def test_no_license_pool_for_non_primary_identifier(self):
        """Overdrive offers licenses, but to get an Overdrive license pool for
        a book you must identify the book by Overdrive's primary
        identifier, not some other kind of identifier.
        """
        assert_raises_regexp(
            ValueError, 
            "License pools for data source 'Overdrive' are keyed to identifier type 'Overdrive ID' \(not 'ISBN', which was provided\)",
            LicensePool.for_foreign_id,
            self._db, DataSource.OVERDRIVE, Identifier.ISBN, "{1-2-3}")

    def test_with_no_work(self):
        p1, ignore = LicensePool.for_foreign_id(
            self._db, DataSource.GUTENBERG, Identifier.GUTENBERG_ID, "1")

        p2, ignore = LicensePool.for_foreign_id(
            self._db, DataSource.OVERDRIVE, Identifier.OVERDRIVE_ID, "2")

        work = self._work(title="Foo")
        p1.work = work
        
        assert p1 in work.license_pools

        eq_([p2], LicensePool.with_no_work(self._db))

    def test_update_availability(self):
        work = self._work(with_license_pool=True)
        work.last_update_time = None

        [pool] = work.license_pools
        pool.update_availability(30, 20, 2, 0)
        eq_(30, pool.licenses_owned)
        eq_(20, pool.licenses_available)
        eq_(2, pool.licenses_reserved)
        eq_(0, pool.patrons_in_hold_queue)

        # Updating availability also modified work.last_update_time.
        assert (datetime.datetime.utcnow() - work.last_update_time) < datetime.timedelta(seconds=2)

    def test_update_availability_triggers_analytics(self):
        with temp_analytics("mock_analytics_provider", {}):
            work = self._work(with_license_pool=True)
            [pool] = work.license_pools
            pool.update_availability(30, 20, 2, 0)
            provider = Analytics.instance().providers[0]
            count = provider.count
            pool.update_availability(30, 21, 2, 0)
            eq_(count + 1, provider.count)
            eq_(CirculationEvent.CHECKIN, provider.event_type)
            pool.update_availability(30, 21, 2, 1)
            eq_(count + 2, provider.count)
            eq_(CirculationEvent.HOLD_PLACE, provider.event_type)

    def test_update_availability_does_nothing_if_given_no_data(self):
        """Passing an empty set of data into update_availability is
        a no-op.
        """

        # Set up a Work.
        work = self._work(with_license_pool=True)
        work.last_update_time = None

        # Set up a LicensePool.
        [pool] = work.license_pools
        pool.last_checked = None
        pool.licenses_owned = 10
        pool.licenses_available = 20
        pool.licenses_reserved = 30
        pool.patrons_in_hold_queue = 40

        # Pass empty values into update_availability.
        pool.update_availability(None, None, None, None)

        # The LicensePool's circulation data is what it was before.
        eq_(10, pool.licenses_owned)
        eq_(20, pool.licenses_available)
        eq_(30, pool.licenses_reserved)
        eq_(40, pool.patrons_in_hold_queue)

        # Work.update_time and LicensePool.last_checked are unaffected.
        eq_(None, work.last_update_time)
        eq_(None, pool.last_checked)

        # If we pass a mix of good and null values...
        pool.update_availability(5, None, None, None)

        # Only the good values are changed.
        eq_(5, pool.licenses_owned)
        eq_(20, pool.licenses_available)
        eq_(30, pool.licenses_reserved)
        eq_(40, pool.patrons_in_hold_queue)


    def test_open_access_links(self):
        edition, pool = self._edition(with_open_access_download=True)
        source = DataSource.lookup(self._db, DataSource.GUTENBERG)

        [oa1] = list(pool.open_access_links)

        # We have one open-access download, let's
        # add another.
        url = self._url
        media_type = Representation.EPUB_MEDIA_TYPE
        link2, new = pool.identifier.add_link(
            Hyperlink.OPEN_ACCESS_DOWNLOAD, url,
            source, pool
        )
        oa2 = link2.resource

        # And let's add a link that's not an open-access download.
        url = self._url
        image, new = pool.identifier.add_link(
            Hyperlink.IMAGE, url, source, pool
        )
        self._db.commit()

        # Only the two open-access download links show up.
        eq_(set([oa1, oa2]), set(pool.open_access_links))

    def test_better_open_access_pool_than(self):

        gutenberg_1 = self._licensepool(
            None, open_access=True, data_source_name=DataSource.GUTENBERG,
            with_open_access_download=True,
        )

        gutenberg_2 = self._licensepool(
            None, open_access=True, data_source_name=DataSource.GUTENBERG,
            with_open_access_download=True,
        )
        
        assert int(gutenberg_1.identifier.identifier) < int(gutenberg_2.identifier.identifier)

        standard_ebooks = self._licensepool(
            None, open_access=True, data_source_name=DataSource.STANDARD_EBOOKS,
            with_open_access_download=True
        )

        overdrive = self._licensepool(
            None, open_access=False, data_source_name=DataSource.OVERDRIVE
        )

        suppressed = self._licensepool(
            None, open_access=True, data_source_name=DataSource.GUTENBERG
        )
        suppressed.suppressed = True

        def better(x,y):
            return x.better_open_access_pool_than(y)

        # We would rather have nothing at all than a suppressed
        # LicensePool.
        eq_(False, better(suppressed, None))

        # A non-open-access LicensePool is not considered at all.
        eq_(False, better(overdrive, None))

        # Something is better than nothing.
        eq_(True, better(gutenberg_1, None))

        # An open access book from a high-quality source beats one
        # from a low-quality source.
        eq_(True, better(standard_ebooks, gutenberg_1))
        eq_(False, better(gutenberg_1, standard_ebooks))

        # A high Gutenberg number beats a low Gutenberg number.
        eq_(True, better(gutenberg_2, gutenberg_1))
        eq_(False, better(gutenberg_1, gutenberg_2))

        # If a supposedly open-access LicensePool doesn't have an
        # open-access download resource, it will only be considered if
        # there is no other alternative.
        no_resource = self._licensepool(
            None, open_access=True, 
            data_source_name=DataSource.STANDARD_EBOOKS,
            with_open_access_download=False,
        )
        eq_(True, better(no_resource, None))
        eq_(False, better(no_resource, gutenberg_1))

    def test_with_complaint(self):
        type = iter(Complaint.VALID_TYPES)
        type1 = next(type)
        type2 = next(type)
        type3 = next(type)

        work1 = self._work(
            "fiction work with complaint",
            language="eng",
            fiction=True,
            with_open_access_download=True)
        lp1 = work1.license_pools[0]
        lp1_complaint1 = self._complaint(
            lp1,
            type1,
            "lp1 complaint1 source",
            "lp1 complaint1 detail")
        lp1_complaint2 = self._complaint(
            lp1,
            type1,
            "lp1 complaint2 source",
            "lp1 complaint2 detail")
        lp1_complaint3 = self._complaint(
            lp1,
            type2,
            "work1 complaint3 source",
            "work1 complaint3 detail")
        lp1_resolved_complaint = self._complaint(
            lp1,
            type3,
            "work3 resolved complaint source",
            "work3 resolved complaint detail",
            datetime.datetime.now())

        work2 = self._work(
            "nonfiction work with complaint",
            language="eng",
            fiction=False,
            with_open_access_download=True)
        lp2 = work2.license_pools[0]
        lp2_complaint1 = self._complaint(
            lp2,
            type2,
            "work2 complaint1 source",
            "work2 complaint1 detail")
        lp2_resolved_complaint = self._complaint(
            lp2,
            type2,
            "work2 resolved complaint source",
            "work2 resolved complaint detail",
            datetime.datetime.now())
        
        work3 = self._work(
            "fiction work without complaint",
            language="eng",
            fiction=True,
            with_open_access_download=True)
        lp3 = work3.license_pools[0]
        lp3_resolved_complaint = self._complaint(
            lp3,
            type3,
            "work3 resolved complaint source",
            "work3 resolved complaint detail",
            datetime.datetime.now())

        work4 = self._work(
            "nonfiction work without complaint",
            language="eng",
            fiction=False,
            with_open_access_download=True)

        # excludes resolved complaints by default
        results = LicensePool.with_complaint(self._db).all()

        eq_(2, len(results))
        eq_(lp1.id, results[0][0].id)
        eq_(3, results[0][1])
        eq_(lp2.id, results[1][0].id)
        eq_(1, results[1][1])

        # include resolved complaints this time
        more_results = LicensePool.with_complaint(self._db, resolved=None).all()

        eq_(3, len(more_results))
        eq_(lp1.id, more_results[0][0].id)
        eq_(4, more_results[0][1])
        eq_(lp2.id, more_results[1][0].id)
        eq_(2, more_results[1][1])
        eq_(lp3.id, more_results[2][0].id)
        eq_(1, more_results[2][1])

        # show only resolved complaints
        resolved_results = LicensePool.with_complaint(self._db, resolved=True).all()
        lp_ids = set([result[0].id for result in resolved_results])
        counts = set([result[1] for result in resolved_results])
        
        eq_(3, len(resolved_results))
        eq_(lp_ids, set([lp1.id, lp2.id, lp3.id]))
        eq_(counts, set([1]))

    def test_editions_in_priority_order(self):
        edition_admin = self._edition(data_source_name=DataSource.LIBRARY_STAFF, with_license_pool=False)
        edition_od, pool = self._edition(data_source_name=DataSource.OVERDRIVE, with_license_pool=True)
        edition_mw = self._edition(data_source_name=DataSource.METADATA_WRANGLER, with_license_pool=False)
        # do not set edition_no_data_source's data source
        edition_no_data_source = self._edition(with_license_pool=False)


        edition_admin.primary_identifier = pool.identifier
        edition_mw.primary_identifier = pool.identifier
        edition_no_data_source.primary_identifier = pool.identifier

        editions_correct = (edition_no_data_source, edition_od, edition_mw, edition_admin)
        editions_contender = pool.editions_in_priority_order()

        #eq_(editions_correct, editions_contender)
        eq_(len(editions_correct), len(editions_contender))

        for index, edition in enumerate(editions_correct):
            eq_(editions_contender[index].title, editions_correct[index].title)

    def test_set_presentation_edition(self):
        """
        Make sure composite edition creation makes good choices when combining 
        field data from provider, metadata wrangler, admin interface, etc. editions.
        """
        # create different types of editions, all with the same identifier
        edition_admin = self._edition(data_source_name=DataSource.LIBRARY_STAFF, with_license_pool=False)
        edition_mw = self._edition(data_source_name=DataSource.METADATA_WRANGLER, with_license_pool=False)
        edition_od, pool = self._edition(data_source_name=DataSource.OVERDRIVE, with_license_pool=True)

        edition_mw.primary_identifier = pool.identifier
        edition_admin.primary_identifier = pool.identifier

        # set overlapping fields on editions
        edition_od.title = u"OverdriveTitle1"

        edition_mw.title = u"MetadataWranglerTitle1"
        edition_mw.subtitle = u"MetadataWranglerSubTitle1"

        edition_admin.title = u"AdminInterfaceTitle1"

        pool.set_presentation_edition()

        edition_composite = pool.presentation_edition

        assert_not_equal(edition_mw, edition_od)
        assert_not_equal(edition_od, edition_admin)
        assert_not_equal(edition_admin, edition_composite)
        assert_not_equal(edition_od, edition_composite)

        # make sure admin pool data had precedence
        eq_(edition_composite.title, u"AdminInterfaceTitle1")
        eq_(edition_admin.contributors, edition_composite.contributors)

        # make sure data not present in the higher-precedence editions didn't overwrite the lower-precedented editions' fields
        eq_(edition_composite.subtitle, u"MetadataWranglerSubTitle1")
        license_pool = edition_composite.is_presentation_for
        eq_(license_pool, pool)

        # Change the admin interface's opinion about who the author
        # is.
        for c in edition_admin.contributions:
            self._db.delete(c)
        self._db.commit()
        [jane], ignore = Contributor.lookup(self._db, u"Doe, Jane")
        jane.family_name, jane.display_name = jane.default_names()
        edition_admin.add_contributor(jane, Contributor.AUTHOR_ROLE)
        pool.set_presentation_edition()

        # The old contributor has been removed from the composite
        # edition, and the new contributor added.
        eq_(set([jane]), edition_composite.contributors)

    def test_circulation_changelog(self):
        
        edition, pool = self._edition(with_license_pool=True)
        pool.licenses_owned = 10
        pool.licenses_available = 9
        pool.licenses_reserved = 8
        pool.patrons_in_hold_queue = 7

        msg, args = pool.circulation_changelog(1, 2, 3, 4)

        # Since all four circulation values changed, the message is as
        # long as it could possibly get.
        eq_(
            'CHANGED %s "%s" %s (%s) %s: %s=>%s %s: %s=>%s %s: %s=>%s %s: %s=>%s',
            msg
        )
        eq_(
            args,
            (edition.medium, edition.title, edition.author, pool.identifier,
             'OWN', 1, 10, 'AVAIL', 2, 9, 'RSRV', 3, 8, 'HOLD', 4, 7)
        )

        # If only one circulation value changes, the message is a lot shorter.
        msg, args = pool.circulation_changelog(10, 9, 8, 15)
        eq_(
            'CHANGED %s "%s" %s (%s) %s: %s=>%s',
            msg
        )
        eq_(
            args,
            (edition.medium, edition.title, edition.author, pool.identifier,
             'HOLD', 15, 7)
        )

        # This works even if, for whatever reason, the edition's
        # bibliographic data is missing.
        edition.title = None
        edition.author = None
        
        msg, args = pool.circulation_changelog(10, 9, 8, 15)
        eq_("[NO TITLE]", args[1])
        eq_("[NO AUTHOR]", args[2])


class TestLicensePoolDeliveryMechanism(DatabaseTest):

    def test_set_rights_status(self):
        edition, pool = self._edition(with_license_pool=True)
        pool.open_access = False
        lpdm = pool.delivery_mechanisms[0]
        uri = RightsStatus.IN_COPYRIGHT
        status = lpdm.set_rights_status(uri)
        eq_(status, lpdm.rights_status)
        eq_(uri, status.uri)
        eq_(RightsStatus.NAMES.get(uri), status.name)
        eq_(False, pool.open_access)

        status2 = lpdm.set_rights_status(uri)
        eq_(status, status2)

        uri2 = "http://unknown"
        status3 = lpdm.set_rights_status(uri2)
        assert status != status3
        eq_(RightsStatus.UNKNOWN, status3.uri)
        eq_(RightsStatus.NAMES.get(RightsStatus.UNKNOWN), status3.name)

        open_access_uri = RightsStatus.GENERIC_OPEN_ACCESS
        open_access_status = lpdm.set_rights_status(open_access_uri)
        eq_(open_access_uri, open_access_status.uri)
        eq_(RightsStatus.NAMES.get(open_access_uri), open_access_status.name)
        eq_(True, pool.open_access)

        non_open_access_status = lpdm.set_rights_status(uri)
        eq_(False, pool.open_access)

        # Add a second license pool, so the pool has one open-access
        # and one commercial delivery mechanism.
        lpdm2 = pool.set_delivery_mechanism(
            Representation.EPUB_MEDIA_TYPE, DeliveryMechanism.NO_DRM,
            RightsStatus.CC_BY, None)
        eq_(2, len(pool.delivery_mechanisms))

        # Now the pool is open access again
        eq_(True, pool.open_access)

        # But if we change the new delivery mechanism to non-open
        # access, the pool won't be open access anymore either.
        lpdm2.set_rights_status(uri)
        eq_(False, pool.open_access)

class TestWork(DatabaseTest):

    def test_all_identifier_ids(self):
        work = self._work(with_license_pool=True)
        lp = work.license_pools[0]
        identifier = self._identifier()
        data_source = DataSource.lookup(self._db, DataSource.OCLC)
        identifier.equivalent_to(data_source, lp.identifier, 1)

        # Make sure there aren't duplicates in the list, if an
        # identifier's equivalent to two of the primary identifiers.
        lp2 = self._licensepool(None)
        work.license_pools.append(lp2)
        identifier.equivalent_to(data_source, lp2.identifier, 1)

        all_identifier_ids = work.all_identifier_ids()
        eq_(3, len(all_identifier_ids))
        eq_(set([lp.identifier.id, lp2.identifier.id, identifier.id]),
            set(all_identifier_ids))

    def test_from_identifiers(self):
        # Prep a work to be identified and a work to be ignored.
        work = self._work(with_license_pool=True, with_open_access_download=True)
        lp = work.license_pools[0]
        ignored_work = self._work(with_license_pool=True, with_open_access_download=True)

        # No identifiers returns None.
        result = Work.from_identifiers(self._db, [])
        eq_(None, result)

        # A work can be found according to its identifier.
        identifiers = [lp.identifier]
        result = Work.from_identifiers(self._db, identifiers).all()
        eq_(1, len(result))
        eq_([work], result)

        # When the work has an equivalent identifier.
        isbn = self._identifier(Identifier.ISBN)
        source = lp.data_source
        lp.identifier.equivalent_to(source, isbn, 1)

        # It can be found according to that equivalency.
        identifiers = [isbn]
        result = Work.from_identifiers(self._db, identifiers).all()
        eq_(1, len(result))
        eq_([work], result)

        # Unless the strength is too low.
        lp.identifier.equivalencies[0].strength = 0.8
        identifiers = [isbn]

        result = Work.from_identifiers(self._db, identifiers).all()
        eq_([], result)

        # Two+ of the same or equivalent identifiers lead to one result.
        identifiers = [lp.identifier, isbn, lp.identifier]
        result = Work.from_identifiers(self._db, identifiers).all()
        eq_(1, len(result))
        eq_([work], result)

        # It accepts a base query.
        qu = self._db.query(Work).join(LicensePool).join(Identifier).\
            filter(LicensePool.suppressed)
        identifiers = [lp.identifier]
        result = Work.from_identifiers(self._db, identifiers, base_query=qu).all()
        # Because the work's license_pool isn't suppressed, it isn't returned.
        eq_([], result)

    def test_calculate_presentation(self):
        """ Test that:
        - work coverage records are made on work creation and primary edition selection.
        - work's presentation information (author, title, etc. fields) does a proper job 
          of combining fields from underlying editions.
        - work's presentation information keeps in sync with work's presentation edition.
        - there can be only one edition that thinks it's the presentation edition for this work.
        - time stamps are stamped.
        """
        gutenberg_source = DataSource.GUTENBERG
        gitenberg_source = DataSource.PROJECT_GITENBERG

        [bob], ignore = Contributor.lookup(self._db, u"Bitshifter, Bob")
        bob.family_name, bob.display_name = bob.default_names()

        edition1, pool1 = self._edition(gitenberg_source, Identifier.GUTENBERG_ID, 
            with_license_pool=True, with_open_access_download=True, authors=[])
        edition1.title = u"The 1st Title"
        edition1.subtitle = u"The 1st Subtitle"
        edition1.add_contributor(bob, Contributor.AUTHOR_ROLE)

        edition2, pool2 = self._edition(gitenberg_source, Identifier.GUTENBERG_ID, 
            with_license_pool=True, with_open_access_download=True, authors=[])
        edition2.title = u"The 2nd Title"
        edition2.subtitle = u"The 2nd Subtitle"
        edition2.add_contributor(bob, Contributor.AUTHOR_ROLE)
        [alice], ignore = Contributor.lookup(self._db, u"Adder, Alice")
        alice.family_name, alice.display_name = alice.default_names()
        edition2.add_contributor(alice, Contributor.AUTHOR_ROLE)

        edition3, pool3 = self._edition(gutenberg_source, Identifier.GUTENBERG_ID, 
            with_license_pool=True, with_open_access_download=True, authors=[])
        edition3.title = u"The 2nd Title"
        edition3.subtitle = u"The 2nd Subtitle"
        edition3.add_contributor(bob, Contributor.AUTHOR_ROLE)
        edition3.add_contributor(alice, Contributor.AUTHOR_ROLE)

        work = self._work(presentation_edition=edition2)
        # add in 3, 2, 1 order to make sure the selection of edition1 as presentation
        # in the second half of the test is based on business logic, not list order.
        for p in pool3, pool1:
            work.license_pools.append(p)

        # This Work starts out with a single CoverageRecord reflecting the
        # work done to generate its initial OPDS entry, and then it adds choose-edition 
        # as a primary edition is set.
        [choose_edition, generate_opds] = sorted(work.coverage_records, key=lambda x: x.operation)
        assert (generate_opds.operation == WorkCoverageRecord.GENERATE_OPDS_OPERATION)
        assert (choose_edition.operation == WorkCoverageRecord.CHOOSE_EDITION_OPERATION)

        # pools aren't yet aware of each other
        eq_(pool1.superceded, False)
        eq_(pool2.superceded, False)
        eq_(pool3.superceded, False)

        work.last_update_time = None
        work.presentation_ready = True
        index = DummyExternalSearchIndex()

        work.calculate_presentation(search_index_client=index)

        # one and only one license pool should be un-superceded
        eq_(pool1.superceded, True)
        eq_(pool2.superceded, False)
        eq_(pool3.superceded, True)

        # sanity check
        eq_(work.presentation_edition, pool2.presentation_edition)
        eq_(work.presentation_edition, edition2)

        # editions that aren't the presentation edition have no work
        eq_(edition1.work, None)
        eq_(edition2.work, work)
        eq_(edition3.work, None)

        # The title of the Work is the title of its primary work record.
        eq_("The 2nd Title", work.title)
        eq_("The 2nd Subtitle", work.subtitle)

        # The author of the Work is the author of its primary work record.
        eq_("Alice Adder, Bob Bitshifter", work.author)
        eq_("Adder, Alice ; Bitshifter, Bob", work.sort_author)

        # The last update time has been set.
        # Updating availability also modified work.last_update_time.
        assert (datetime.datetime.utcnow() - work.last_update_time) < datetime.timedelta(seconds=2)

        # The index has been updated with a document.
        [[args, doc]] = index.docs.items()
        eq_(doc, work.to_search_document())

        # The Work now has a complete set of WorkCoverageRecords
        # associated with it, reflecting all the operations that
        # occured as part of calculate_presentation().
        records = work.coverage_records
        expect = set([
            WorkCoverageRecord.CHOOSE_EDITION_OPERATION,
            WorkCoverageRecord.CLASSIFY_OPERATION,
            WorkCoverageRecord.SUMMARY_OPERATION,
            WorkCoverageRecord.QUALITY_OPERATION,
            WorkCoverageRecord.GENERATE_OPDS_OPERATION,
            WorkCoverageRecord.UPDATE_SEARCH_INDEX_OPERATION + "-" + index.works_index,
        ])
        eq_(expect, set([x.operation for x in records]))
        
        # Now mark the pool with the presentation edition as suppressed.
        # work.calculate_presentation() will call work.mark_licensepools_as_superceded(), 
        # which will mark the suppressed pool as superceded and take its edition out of the running.
        # Make sure that work's presentation edition and work's author, etc. 
        # fields are updated accordingly, and that the superceded pool's edition 
        # knows it's no longer the champ.
        pool2.suppressed = True
        
        work.calculate_presentation(search_index_client=index)

        # The title of the Work is the title of its primary work record.
        eq_("The 1st Title", work.title)
        eq_("The 1st Subtitle", work.subtitle)

        # author of composite edition is still Alice and Bob combined
        eq_("Bob Bitshifter", work.author)
        eq_("Bitshifter, Bob", work.sort_author)

        # sanity check
        eq_(work.presentation_edition, pool1.presentation_edition)
        eq_(work.presentation_edition, edition1)

        # editions that aren't the presentation edition have no work
        eq_(edition1.work, work)
        eq_(edition2.work, None)
        eq_(edition3.work, None)

        # The last update time has been set.
        # Updating availability also modified work.last_update_time.
        assert (datetime.datetime.utcnow() - work.last_update_time) < datetime.timedelta(seconds=2)

    def test_set_presentation_ready(self):

        work = self._work(with_license_pool=True)

        search = DummyExternalSearchIndex()
        # This is how the work will be represented in the dummy search
        # index.
        index_key = (search.works_index, 
                     DummyExternalSearchIndex.work_document_type,
                     work.id)

        presentation = work.presentation_edition
        work.set_presentation_ready_based_on_content(search_index_client=search)
        eq_(True, work.presentation_ready)

        # The work has been added to the search index.
        eq_([index_key], search.docs.keys())
        
        # This work is presentation ready because it has a title
        # and a fiction status.

        # Remove the title, and the work stops being presentation
        # ready.
        presentation.title = None
        work.set_presentation_ready_based_on_content(search_index_client=search)
        eq_(False, work.presentation_ready)        

        # The work has been removed from the search index.
        eq_([], search.docs.keys())

        # Restore the title, and everything is fixed.
        presentation.title = u"foo"
        work.set_presentation_ready_based_on_content(search_index_client=search)
        eq_(True, work.presentation_ready)        
        eq_([index_key], search.docs.keys())

        # Remove the fiction status, and the work stops being
        # presentation ready.
        work.fiction = None
        work.set_presentation_ready_based_on_content(search_index_client=search)
        eq_(False, work.presentation_ready)        

        # It's gone from the search index again.
        eq_([], search.docs.keys())

        # Restore the fiction status, and everything is fixed.
        work.fiction = False
        work.set_presentation_ready_based_on_content(search_index_client=search)

        eq_(True, work.presentation_ready)
        eq_([index_key], search.docs.keys())

    def test_assign_genres_from_weights(self):
        work = self._work()

        # This work was once classified under Fantasy and Romance.        
        work.assign_genres_from_weights({Romance : 1000, Fantasy : 1000})
        self._db.commit()
        before = sorted((x.genre.name, x.affinity) for x in work.work_genres)
        eq_([(u'Fantasy', 0.5), (u'Romance', 0.5)], before)

        # But now it's classified under Science Fiction and Romance.
        work.assign_genres_from_weights({Romance : 100, Science_Fiction : 300})
        self._db.commit()
        after = sorted((x.genre.name, x.affinity) for x in work.work_genres)
        eq_([(u'Romance', 0.25), (u'Science Fiction', 0.75)], after)

    def test_classifications_with_genre(self):
        work = self._work(with_open_access_download=True)
        identifier = work.presentation_edition.primary_identifier
        genres = self._db.query(Genre).all()
        subject1 = self._subject(type="type1", identifier="subject1")
        subject1.genre = genres[0]
        subject2 = self._subject(type="type2", identifier="subject2")
        subject2.genre = genres[1]
        subject3 = self._subject(type="type2", identifier="subject3")
        subject3.genre = None
        source = DataSource.lookup(self._db, DataSource.AXIS_360)        
        classification1 = self._classification(
            identifier=identifier, subject=subject1, 
            data_source=source, weight=1)
        classification2 = self._classification(
            identifier=identifier, subject=subject2, 
            data_source=source, weight=2)
        classification3 = self._classification(
            identifier=identifier, subject=subject3, 
            data_source=source, weight=2)

        results = work.classifications_with_genre().all()
        
        eq_([classification2, classification1], results)

    def test_mark_licensepools_as_superceded(self):
        # A commercial LP that somehow got superceded will be
        # un-superceded.
        commercial = self._licensepool(
            None, data_source_name=DataSource.OVERDRIVE
        )
        work, is_new = commercial.calculate_work()
        commercial.superceded = True
        work.mark_licensepools_as_superceded()
        eq_(False, commercial.superceded)

        # An open-access LP that was superceded will be un-superceded if
        # chosen.
        gutenberg = self._licensepool(
            None, data_source_name=DataSource.GUTENBERG,
            open_access=True, with_open_access_download=True
        )
        work, is_new = gutenberg.calculate_work()
        gutenberg.superceded = True
        work.mark_licensepools_as_superceded()
        eq_(False, gutenberg.superceded)

        # Of two open-access LPs, the one from the higher-quality data
        # source will be un-superceded, and the one from the
        # lower-quality data source will be superceded.
        standard_ebooks = self._licensepool(
            None, data_source_name=DataSource.STANDARD_EBOOKS,
            open_access=True, with_open_access_download=True
        )
        work.license_pools.append(standard_ebooks)
        gutenberg.superceded = False
        standard_ebooks.superceded = True
        work.mark_licensepools_as_superceded()
        eq_(True, gutenberg.superceded)
        eq_(False, standard_ebooks.superceded)

        # Of three open-access pools, 1 and only 1 will be chosen as non-superceded.
        gitenberg1 = self._licensepool(edition=None, open_access=True, 
            data_source_name=DataSource.PROJECT_GITENBERG, with_open_access_download=True
        )

        gitenberg2 = self._licensepool(edition=None, open_access=True, 
            data_source_name=DataSource.PROJECT_GITENBERG, with_open_access_download=True
        )

        gutenberg1 = self._licensepool(edition=None, open_access=True, 
            data_source_name=DataSource.GUTENBERG, with_open_access_download=True
        )

        work_multipool = self._work(presentation_edition=None)
        work_multipool.license_pools.append(gutenberg1)
        work_multipool.license_pools.append(gitenberg2)
        work_multipool.license_pools.append(gitenberg1)

        # pools aren't yet aware of each other
        eq_(gutenberg1.superceded, False)
        eq_(gitenberg1.superceded, False)
        eq_(gitenberg2.superceded, False)

        # make pools figure out who's best
        work_multipool.mark_licensepools_as_superceded()

        eq_(gutenberg1.superceded, True)
        # There's no way to choose between the two gitenberg pools, 
        # so making sure only one has been chosen is enough. 
        chosen_count = 0
        for chosen_pool in gutenberg1, gitenberg1, gitenberg2:
            if chosen_pool.superceded is False:
                chosen_count += 1;
        eq_(chosen_count, 1)

        # throw wrench in
        gitenberg1.suppressed = True

        # recalculate bests
        work_multipool.mark_licensepools_as_superceded()
        eq_(gutenberg1.superceded, True)
        eq_(gitenberg1.superceded, True)
        eq_(gitenberg2.superceded, False)

    def test_work_remains_viable_on_pools_suppressed(self):
        """ If a work has all of its pools suppressed, the work's author, title, 
        and subtitle still have the last best-known info in them.
        """
        (work, pool_std_ebooks, pool_git, pool_gut, 
            edition_std_ebooks, edition_git, edition_gut, alice, bob) = self._sample_ecosystem()

        # make sure the setup is what we expect
        eq_(pool_std_ebooks.suppressed, False)
        eq_(pool_git.suppressed, False)
        eq_(pool_gut.suppressed, False)

        # sanity check - we like standard ebooks and it got determined to be the best
        eq_(work.presentation_edition, pool_std_ebooks.presentation_edition)
        eq_(work.presentation_edition, edition_std_ebooks)

        # editions know who's the presentation edition
        eq_(edition_std_ebooks.work, work)
        eq_(edition_git.work, None)
        eq_(edition_gut.work, None)

        # The title of the Work is the title of its presentation edition.
        eq_("The Standard Ebooks Title", work.title)
        eq_("The Standard Ebooks Subtitle", work.subtitle)

        # The author of the Work is the author of its presentation edition.
        eq_("Alice Adder", work.author)
        eq_("Adder, Alice", work.sort_author)

        # now suppress all of the license pools
        pool_std_ebooks.suppressed = True
        pool_git.suppressed = True
        pool_gut.suppressed = True

        # and let work know
        work.calculate_presentation()

        # standard ebooks was last viable pool, and it stayed as work's choice
        eq_(work.presentation_edition, pool_std_ebooks.presentation_edition)
        eq_(work.presentation_edition, edition_std_ebooks)

        # editions know who's the presentation edition
        eq_(edition_std_ebooks.work, work)
        eq_(edition_git.work, None)
        eq_(edition_gut.work, None)

        # The title of the Work is still the title of its last viable presentation edition.
        eq_("The Standard Ebooks Title", work.title)
        eq_("The Standard Ebooks Subtitle", work.subtitle)

        # The author of the Work is still the author of its last viable presentation edition.
        eq_("Alice Adder", work.author)
        eq_("Adder, Alice", work.sort_author)

    def test_work_updates_info_on_pool_suppressed(self):
        """ If the provider of the work's presentation edition gets suppressed, 
        the work will choose another child license pool's presentation edition as 
        its presentation edition.
        """
        (work, pool_std_ebooks, pool_git, pool_gut, 
            edition_std_ebooks, edition_git, edition_gut, alice, bob) = self._sample_ecosystem()

        # make sure the setup is what we expect
        eq_(pool_std_ebooks.suppressed, False)
        eq_(pool_git.suppressed, False)
        eq_(pool_gut.suppressed, False)

        # sanity check - we like standard ebooks and it got determined to be the best
        eq_(work.presentation_edition, pool_std_ebooks.presentation_edition)
        eq_(work.presentation_edition, edition_std_ebooks)

        # editions know who's the presentation edition
        eq_(edition_std_ebooks.work, work)
        eq_(edition_git.work, None)
        eq_(edition_gut.work, None)

        # The title of the Work is the title of its presentation edition.
        eq_("The Standard Ebooks Title", work.title)
        eq_("The Standard Ebooks Subtitle", work.subtitle)

        # The author of the Work is the author of its presentation edition.
        eq_("Alice Adder", work.author)
        eq_("Adder, Alice", work.sort_author)

        # now suppress the primary license pool
        pool_std_ebooks.suppressed = True

        # and let work know
        work.calculate_presentation()

        # gitenberg is next best and it got determined to be the best
        eq_(work.presentation_edition, pool_git.presentation_edition)
        eq_(work.presentation_edition, edition_git)

        # editions know who's the presentation edition
        eq_(edition_std_ebooks.work, None)
        eq_(edition_git.work, work)
        eq_(edition_gut.work, None)

        # The title of the Work is still the title of its last viable presentation edition.
        eq_("The GItenberg Title", work.title)
        eq_("The GItenberg Subtitle", work.subtitle)

        # The author of the Work is still the author of its last viable presentation edition.
        eq_("Alice Adder, Bob Bitshifter", work.author)
        eq_("Adder, Alice ; Bitshifter, Bob", work.sort_author)

    def test_missing_coverage_from(self):
        operation = 'the_operation'

        # Here's a work with a coverage record.
        work = self._work(with_license_pool=True)

        # It needs coverage.
        eq_([work], Work.missing_coverage_from(self._db, operation).all())

        # Let's give it coverage.
        record = self._work_coverage_record(work, operation)

        # It no longer needs coverage!
        eq_([], Work.missing_coverage_from(self._db, operation).all())

        # But if we disqualify coverage records created before a 
        # certain time, it might need coverage again.
        cutoff = record.timestamp + datetime.timedelta(seconds=1)

        eq_(
            [work], Work.missing_coverage_from(
                self._db, operation, count_as_missing_before=cutoff
            ).all()
        )

    def test_top_genre(self):
        work = self._work()
        identifier = work.presentation_edition.primary_identifier
        genres = self._db.query(Genre).all()
        source = DataSource.lookup(self._db, DataSource.AXIS_360)

        # returns None when work has no genres
        eq_(None, work.top_genre())

        # returns only genre
        wg1, is_new = get_one_or_create(
            self._db, WorkGenre, work=work, genre=genres[0], affinity=1
        )
        eq_(genres[0].name, work.top_genre())

        # returns top genre
        wg1.affinity = 0.2
        wg2, is_new = get_one_or_create(
            self._db, WorkGenre, work=work, genre=genres[1], affinity=0.8
        )
        eq_(genres[1].name, work.top_genre())

    def test_to_search_document(self):
        # Set up an edition and work.
        edition, pool = self._edition(authors=[self._str, self._str], with_license_pool=True)
        work = self._work(presentation_edition=edition)

        # These are the edition's authors.
        [contributor1] = [c.contributor for c in edition.contributions if c.role == Contributor.PRIMARY_AUTHOR_ROLE]
        contributor1.family_name = self._str
        [contributor2] = [c.contributor for c in edition.contributions if c.role == Contributor.AUTHOR_ROLE]

        data_source = DataSource.lookup(self._db, DataSource.THREEM)
        
        # This identifier is strongly equivalent to the edition's.
        identifier = self._identifier()
        identifier.equivalent_to(data_source, edition.primary_identifier, 0.9)

        # This identifier is equivalent to the other identifier, but the strength
        # is too weak for it to be used.
        identifier2 = self._identifier()
        identifier.equivalent_to(data_source, identifier, 0.1)

        # Add some classifications.

        # This classification has no subject name, so the search document will use the subject identifier.
        edition.primary_identifier.classify(data_source, Subject.THREEM, "FICTION/Science Fiction/Time Travel", None, 6)

        # This one has the same subject type and identifier, so their weights will be combined.
        identifier.classify(data_source, Subject.THREEM, "FICTION/Science Fiction/Time Travel", None, 1)

        # Here's another classification with a different subject type.
        edition.primary_identifier.classify(data_source, Subject.OVERDRIVE, "Romance", None, 2)

        # This classification has a subject name, so the search document will use that instead of the identifier.
        identifier.classify(data_source, Subject.FAST, self._str, "Sea Stories", 7)

        # This classification will be left out because its subject type isn't useful for search.
        identifier.classify(data_source, Subject.DDC, self._str, None)

        # This classification will be left out because its identifier isn't sufficiently equivalent to the edition's.
        identifier2.classify(data_source, Subject.FAST, self._str, None)

        # Add some genres.
        genre1, ignore = Genre.lookup(self._db, "Science Fiction")
        genre2, ignore = Genre.lookup(self._db, "Romance")
        work.genres = [genre1, genre2]
        work.work_genres[0].affinity = 1

        # Add the other fields used in the search document.
        work.target_age = NumericRange(7, 8, '[]')
        edition.subtitle = self._str
        edition.series = self._str
        edition.publisher = self._str
        edition.imprint = self._str
        work.fiction = False
        work.audience = Classifier.AUDIENCE_YOUNG_ADULT
        work.summary_text = self._str
        work.rating = 5
        work.popularity = 4

        # Make sure all of this will show up in a database query.
        self._db.flush()


        search_doc = work.to_search_document()
        eq_(work.id, search_doc['_id'])
        eq_(work.title, search_doc['title'])
        eq_(edition.subtitle, search_doc['subtitle'])
        eq_(edition.series, search_doc['series'])
        eq_(edition.language, search_doc['language'])
        eq_(work.sort_title, search_doc['sort_title'])
        eq_(work.author, search_doc['author'])
        eq_(work.sort_author, search_doc['sort_author'])
        eq_(edition.medium, search_doc['medium'])
        eq_(edition.publisher, search_doc['publisher'])
        eq_(edition.imprint, search_doc['imprint'])
        eq_(edition.permanent_work_id, search_doc['permanent_work_id'])
        eq_("Nonfiction", search_doc['fiction'])
        eq_("YoungAdult", search_doc['audience'])
        eq_(work.summary_text, search_doc['summary'])
        eq_(work.quality, search_doc['quality'])
        eq_(work.rating, search_doc['rating'])
        eq_(work.popularity, search_doc['popularity'])

        contributors = search_doc['contributors']
        eq_(2, len(contributors))
        [contributor1_doc] = [c for c in contributors if c['sort_name'] == contributor1.sort_name]
        [contributor2_doc] = [c for c in contributors if c['sort_name'] == contributor2.sort_name]
        eq_(contributor1.family_name, contributor1_doc['family_name'])
        eq_(None, contributor2_doc['family_name'])
        eq_(Contributor.PRIMARY_AUTHOR_ROLE, contributor1_doc['role'])
        eq_(Contributor.AUTHOR_ROLE, contributor2_doc['role'])

        classifications = search_doc['classifications']
        eq_(3, len(classifications))
        [classification1_doc] = [c for c in classifications if c['scheme'] == Subject.uri_lookup[Subject.THREEM]]
        [classification2_doc] = [c for c in classifications if c['scheme'] == Subject.uri_lookup[Subject.OVERDRIVE]]
        [classification3_doc] = [c for c in classifications if c['scheme'] == Subject.uri_lookup[Subject.FAST]]
        eq_("FICTION Science Fiction Time Travel", classification1_doc['term'])
        eq_(float(6 + 1)/(6 + 1 + 2 + 7), classification1_doc['weight'])
        eq_("Romance", classification2_doc['term'])
        eq_(float(2)/(6 + 1 + 2 + 7), classification2_doc['weight'])
        eq_("Sea Stories", classification3_doc['term'])
        eq_(float(7)/(6 + 1 + 2 + 7), classification3_doc['weight'])
        
        genres = search_doc['genres']
        eq_(2, len(genres))
        [genre1_doc] = [g for g in genres if g['name'] == genre1.name]
        [genre2_doc] = [g for g in genres if g['name'] == genre2.name]
        eq_(Subject.SIMPLIFIED_GENRE, genre1_doc['scheme'])
        eq_(genre1.id, genre1_doc['term'])
        eq_(1, genre1_doc['weight'])
        eq_(Subject.SIMPLIFIED_GENRE, genre2_doc['scheme'])
        eq_(genre2.id, genre2_doc['term'])
        eq_(0, genre2_doc['weight'])

        target_age_doc = search_doc['target_age']
        eq_(work.target_age.lower, target_age_doc['lower'])
        eq_(work.target_age.upper, target_age_doc['upper'])


class TestCirculationEvent(DatabaseTest):

    def _event_data(self, **kwargs):
        for k, default in (
                ("source", DataSource.OVERDRIVE),
                ("id_type", Identifier.OVERDRIVE_ID),
                ("start", datetime.datetime.utcnow()),
                ("type", CirculationEvent.LICENSE_ADD),
        ):
            kwargs.setdefault(k, default)
        if 'old_value' in kwargs and 'new_value' in kwargs:
            kwargs['delta'] = kwargs['new_value'] - kwargs['old_value']
        return kwargs

    def _get_datetime(self, data, key):
        date = data.get(key, None)
        if not date:
            return None
        elif isinstance(date, datetime.date):
            return date
        else:
            return datetime.datetime.strptime(date, CirculationEvent.TIME_FORMAT)

    def _get_int(self, data, key):
        value = data.get(key, None)
        if not value:
            return value
        else:
            return int(value)

    def from_dict(self, data):
        _db = self._db

        # Identify the source of the event.
        source_name = data['source']
        source = DataSource.lookup(_db, source_name)

        # Identify which LicensePool the event is talking about.
        foreign_id = data['id']
        identifier_type = source.primary_identifier_type

        license_pool, was_new = LicensePool.for_foreign_id(
            _db, source, identifier_type, foreign_id)

        # Finally, gather some information about the event itself.
        type = data.get("type")
        start = self._get_datetime(data, 'start')
        end = self._get_datetime(data, 'end')
        old_value = self._get_int(data, 'old_value')
        new_value = self._get_int(data, 'new_value')
        delta = self._get_int(data, 'delta')
        foreign_patron_id = data.get("foreign_patron_id")
        event, was_new = get_one_or_create(
            _db, CirculationEvent, license_pool=license_pool,
            type=type, start=start, foreign_patron_id=foreign_patron_id,
            create_method_kwargs=dict(
                old_value=old_value,
                new_value=new_value,
                delta=delta,
                end=end)
            )
        return event, was_new

    def test_new_title(self):

        # Here's a new title.
        data = self._event_data(
            source=DataSource.OVERDRIVE,
            id="{1-2-3}",
            type=CirculationEvent.LICENSE_ADD,
            old_value=0,
            delta=2,
            new_value=2,
        )
        
        # Turn it into an event and see what happens.
        event, ignore = self.from_dict(data)

        # The event is associated with the correct data source.
        eq_(DataSource.OVERDRIVE, event.license_pool.data_source.name)

        # The event identifies a work by its ID plus the data source's
        # primary identifier.
        eq_(Identifier.OVERDRIVE_ID, event.license_pool.identifier.type)
        eq_("{1-2-3}", event.license_pool.identifier.identifier)

        # The number of licenses has not been set to the new value.
        # The creator of a circulation event is responsible for also
        # updating the dataset.
        eq_(0, event.license_pool.licenses_owned)


# class TestWorkQuality(DatabaseTest):

#     def test_better_known_work_gets_higher_rating(self):

#         gutenberg_source = DataSource.lookup(self._db, DataSource.GUTENBERG)

#         edition1_1, pool1 = self._edition(with_license_pool=True)
#         edition1_2 = self._edition(with_license_pool=False)

#         edition2_1, pool2 = self._edition(with_license_pool=True)

#         wrs = []
#         pools = []
#         for i in range(10):
#             wr, pool = self._edition(with_license_pool=True)
#             wrs.append(wr)
#             pools.append(pool)

#         work1 = Work()
#         work1.editions.extend([edition1_1, edition1_2] + wrs)
#         work1.license_pools.extend(pools + [pool1])

#         work2 = Work()
#         work2.editions.append(edition2_1)
#         work2.license_pools.append(pool2)

#         work1.calculate_presentation()
#         work2.calculate_presentation()

#         assert work1.quality > work2.quality

#     def test_more_license_pools_gets_higher_rating(self):

#         gutenberg_source = DataSource.lookup(self._db, DataSource.GUTENBERG)

#         edition1_1, pool1 = self._edition(with_license_pool=True)
#         edition1_2, pool2 = self._edition(with_license_pool=True)

#         edition2_1, pool3 = self._edition(with_license_pool=True)
#         edition2_2 = self._edition(with_license_pool=False)

#         wrs = []
#         pools = []
#         for i in range(10):
#             wr, pool = self._edition(with_license_pool=True)
#             wrs.append(wr)
#             pools.append(pool)

#         work1 = Work()
#         work1.editions.extend([edition1_1, edition1_2] + wrs)
#         work1.license_pools.extend([pool1, pool2] + pools)

#         work2 = Work()
#         work2.editions.extend([edition2_1, edition2_2])
#         work2.license_pools.extend([pool3])

#         work1.calculate_presentation()
#         work2.calculate_presentation()

#         assert work1.quality > work2.quality


class TestWorkConsolidation(DatabaseTest):

    def test_calculate_work_success(self):
        e, p = self._edition(with_license_pool=True)
        work, new = p.calculate_work(even_if_no_author=True)
        eq_(p.presentation_edition, work.presentation_edition)
        eq_(True, new)

    def test_calculate_work_bails_out_if_no_title(self):
        e, p = self._edition(with_license_pool=True)
        e.title=None
        work, new = p.calculate_work(even_if_no_author=True)
        eq_(None, work)
        eq_(False, new)

    def test_calculate_work_bails_out_if_no_author(self):
        e, p = self._edition(with_license_pool=True, authors=[])
        work, new = p.calculate_work(even_if_no_author=False)
        eq_(None, work)
        eq_(False, new)

        # If we know that there simply is no author for this work,
        # we can pass in even_if_no_author=True
        work, new = p.calculate_work(even_if_no_author=True)
        eq_(p.presentation_edition, work.presentation_edition)
        eq_(True, new)


    def test_calculate_work_matches_based_on_permanent_work_id(self):
        # Here are two Editions with the same permanent work ID, 
        # since they have the same title/author.
        edition1, ignore = self._edition(with_license_pool=True)
        edition2, ignore = self._edition(
            title=edition1.title, authors=edition1.author,
            with_license_pool=True)

        # Calling calculate_work() on the first edition creates a Work.
        work1, created = edition1.license_pool.calculate_work()
        eq_(created, True)

        # Calling calculate_work() on the second edition associated
        # the second edition's pool with the first work.
        work2, created = edition2.license_pool.calculate_work()
        eq_(created, False)

        eq_(work1, work2)

        eq_(set([edition1.license_pool, edition2.license_pool]), set(work1.license_pools))


    def test_calculate_work_for_licensepool_creates_new_work(self):
        edition1, ignore = self._edition(data_source_name=DataSource.GUTENBERG, identifier_type=Identifier.GUTENBERG_ID, 
            title=self._str, authors=[self._str], with_license_pool=True)

        # This edition is unique to the existing work.
        preexisting_work = Work()
        preexisting_work.set_presentation_edition(edition1)

        # This edition is unique to the new LicensePool
        edition2, pool = self._edition(data_source_name=DataSource.GUTENBERG, identifier_type=Identifier.GUTENBERG_ID, 
            title=self._str, authors=[self._str], with_license_pool=True)

        # Call calculate_work(), and a new Work is created.
        work, created = pool.calculate_work()
        eq_(True, created)
        assert work != preexisting_work



    def test_calculate_work_does_nothing_unless_edition_has_title_and_author(self):
        edition, ignore = Edition.for_foreign_id(
            self._db, DataSource.GUTENBERG, Identifier.GUTENBERG_ID, "1")
        pool, ignore = LicensePool.for_foreign_id(
            self._db, DataSource.GUTENBERG, Identifier.GUTENBERG_ID, "1")
        work, created = pool.calculate_work()
        eq_(None, work)

        edition.title = u"foo"
        work, created = pool.calculate_work()
        eq_(None, work)

        edition.add_contributor(u"bar", Contributor.PRIMARY_AUTHOR_ROLE)
        edition.calculate_presentation()
        work, created = pool.calculate_work()
        eq_(True, created)

        # The edition is the work's presentation edition.
        eq_(work, edition.work)
        eq_(edition, work.presentation_edition)
        eq_(u"foo", work.title)
        eq_(u"bar", work.author)

    def test_calculate_work_can_be_forced_to_work_with_no_author(self):
        edition, ignore = Edition.for_foreign_id(
            self._db, DataSource.GUTENBERG, Identifier.GUTENBERG_ID, "1")
        pool, ignore = LicensePool.for_foreign_id(
            self._db, DataSource.GUTENBERG, Identifier.GUTENBERG_ID, "1")
        work, created = pool.calculate_work()
        eq_(None, work)

        edition.title = u"foo"
        work, created = pool.calculate_work(even_if_no_author=True)
        eq_(True, created)
        self._db.commit()
        eq_(edition, work.presentation_edition)
        eq_(u"foo", work.title)
        eq_(Edition.UNKNOWN_AUTHOR, work.author)

    def test_calculate_work_for_new_work(self):
        # TODO: This test doesn't actually test
        # anything. calculate_work() is too complicated and needs to
        # be refactored.

        # This work record is unique to the existing work.
        edition1, ignore = Edition.for_foreign_id(
            self._db, DataSource.GUTENBERG, Identifier.GUTENBERG_ID, "1")

        # This work record is shared by the existing work and the new
        # LicensePool.
        edition2, ignore = Edition.for_foreign_id(
            self._db, DataSource.GUTENBERG, Identifier.GUTENBERG_ID, "2")

        # These work records are unique to the new LicensePool.

        edition3, ignore = Edition.for_foreign_id(
            self._db, DataSource.GUTENBERG, Identifier.GUTENBERG_ID, "3")

        edition4, ignore = Edition.for_foreign_id(
            self._db, DataSource.GUTENBERG, Identifier.GUTENBERG_ID, "4")

        # Make edition4's primary identifier equivalent to edition3's and edition1's
        # primaries.
        data_source = DataSource.lookup(self._db, DataSource.OCLC_LINKED_DATA)
        for make_equivalent in edition3, edition1:
            edition4.primary_identifier.equivalent_to(
                data_source, make_equivalent.primary_identifier, 1)
        preexisting_work = self._work(presentation_edition=edition1)

        pool, ignore = LicensePool.for_foreign_id(
            self._db, DataSource.GUTENBERG, Identifier.GUTENBERG_ID, "4")
        self._db.commit()

        pool.calculate_work()

    def test_open_access_pools_grouped_together(self):

        # We have four editions with exactly the same title and author.
        # Two of them are open-access, two are not.
        title = "The Only Title"
        author = "Single Author"
        ed1, open1 = self._edition(title=title, authors=author, with_license_pool=True)
        ed2, open2 = self._edition(title=title, authors=author, with_license_pool=True)
        ed3, restricted3 = self._edition(
            title=title, authors=author, data_source_name=DataSource.OVERDRIVE,
            with_license_pool=True)
        ed4, restricted4 = self._edition(
            title=title, authors=author, data_source_name=DataSource.OVERDRIVE,
            with_license_pool=True)

        restricted3.open_access = False
        restricted4.open_access = False

        # Every identifier is equivalent to every other identifier.
        s = DataSource.lookup(self._db, DataSource.OCLC_LINKED_DATA)
        ed1.primary_identifier.equivalent_to(s, ed2.primary_identifier, 1)
        ed1.primary_identifier.equivalent_to(s, ed3.primary_identifier, 1)
        ed1.primary_identifier.equivalent_to(s, ed4.primary_identifier, 1)
        ed2.primary_identifier.equivalent_to(s, ed3.primary_identifier, 1)
        ed2.primary_identifier.equivalent_to(s, ed4.primary_identifier, 1)
        ed3.primary_identifier.equivalent_to(s, ed4.primary_identifier, 1)

        open1.calculate_work()
        open2.calculate_work()
        restricted3.calculate_work()
        restricted4.calculate_work()

        assert open1.work != None
        assert open2.work != None
        assert restricted3.work != None
        assert restricted4.work != None

        # The two open-access pools are grouped together.
        eq_(open1.work, open2.work)

        # Each restricted-access pool is completely isolated.
        assert restricted3.work != restricted4.work
        assert restricted3.work != open1.work

    def test_calculate_work_fixes_work_in_invalid_state(self):
        # Here's a Work with a commercial edition of "abcd".
        work = self._work(with_license_pool=True)
        [abcd_commercial] = work.license_pools
        abcd_commercial.open_access = False
        abcd_commercial.presentation_edition.permanent_work_id = "abcd"

        # Due to a earlier error, the Work also contains a _second_
        # commercial edition of "abcd"...
        edition, abcd_commercial_2 = self._edition(with_license_pool=True)
        abcd_commercial_2.open_access = False
        abcd_commercial_2.presentation_edition.permanent_work_id = "abcd"
        work.license_pools.append(abcd_commercial_2)

        # ...as well as an open-access edition of "abcd".
        edition, abcd_open_access = self._edition(
            with_license_pool=True, with_open_access_download=True
        )
        abcd_open_access.open_access = True
        abcd_open_access.presentation_edition.permanent_work_id = "abcd"
        work.license_pools.append(abcd_open_access)

        # calculate_work() recalculates the permanent work ID of a
        # LicensePool's presentation edition, and obviously the real
        # value isn't "abcd" for any of these Editions. Mocking
        # calculate_permanent_work_id ensures that we run the code
        # under the assumption that all these Editions have the same
        # permanent work ID.
        def mock_pwid(debug=False):
            return "abcd"
        for lp in [abcd_commercial, abcd_commercial_2, abcd_open_access]:
            lp.presentation_edition.calculate_permanent_work_id = mock_pwid

        # Anyway, we can fix the whole problem by calling
        # calculate_work() on one of the LicensePools.
        work_after, is_new = abcd_commercial.calculate_work()
        eq_(work_after, work)
        eq_(False, is_new)

        # The LicensePool we called calculate_work() on gets to stay
        # in the Work, but the other two have been kicked out and
        # given their own works.
        assert abcd_commercial_2.work != work
        assert abcd_open_access.work != work

        # The commercial LicensePool has been given a Work of its own.
        eq_([abcd_commercial_2], abcd_commercial_2.work.license_pools)

        # The open-access work has been given the Work that will be
        # used for all open-access LicensePools for that book going
        # forward.

        expect_open_access_work, open_access_work_is_new = (
            Work.open_access_for_permanent_work_id(
                self._db, "abcd", Edition.BOOK_MEDIUM
            )
        )
        eq_(expect_open_access_work, abcd_open_access.work)

        # Now we're going to restore the bad configuration, where all
        # three books have the same Work. This time we're going to
        # call calculate_work() on the open-access LicensePool, and
        # verify that we get similar results as when we call
        # calculate_work() on one of the commercial LicensePools.
        abcd_commercial_2.work = work
        abcd_open_access.work = work

        work_after, is_new = abcd_open_access.calculate_work()
        # Since we called calculate_work() on the open-access work, it
        # maintained control of the Work, and both commercial books
        # got assigned new Works.
        eq_(work, work_after)
        eq_(False, is_new)

        assert abcd_commercial.work != work
        assert abcd_commercial.work != None
        assert abcd_commercial_2.work != work
        assert abcd_commercial_2.work != None
        assert abcd_commercial.work != abcd_commercial_2.work

        # Finally, let's test that nothing happens if you call
        # calculate_work() on a self-consistent situation.
        open_access_work = abcd_open_access.work
        eq_((open_access_work, False), abcd_open_access.calculate_work())

        commercial_work = abcd_commercial.work
        eq_((commercial_work, False), abcd_commercial.calculate_work())

    def test_calculate_work_fixes_book_grouped_with_audiobook(self):
        # Here's a Work with an open-access edition of "abcd".
        work = self._work(with_license_pool=True)
        [book] = work.license_pools
        book.presentation_edition.permanent_work_id = "abcd"

        # Due to a earlier error, the Work also contains an
        # open-access _audiobook_ of "abcd".
        edition, audiobook = self._edition(with_license_pool=True)
        audiobook.presentation_edition.medium=Edition.AUDIO_MEDIUM
        audiobook.presentation_edition.permanent_work_id = "abcd"
        work.license_pools.append(audiobook)

        def mock_pwid(debug=False):
            return "abcd"
        for lp in [book, audiobook]:
            lp.presentation_edition.calculate_permanent_work_id = mock_pwid

        # We can fix this by calling calculate_work() on one of the
        # LicensePools.
        work_after, is_new = book.calculate_work()
        eq_(work_after, work)
        eq_(False, is_new)

        # The LicensePool we called calculate_work() on gets to stay
        # in the Work, but the other one has been kicked out and
        # given its own work.
        eq_(book.work, work)
        assert audiobook.work != work

        # The audiobook LicensePool has been given a Work of its own.
        eq_([audiobook], audiobook.work.license_pools)

        # The book has been given the Work that will be used for all
        # book-type LicensePools for that title going forward.
        expect_book_work, book_work_is_new = (
            Work.open_access_for_permanent_work_id(
                self._db, "abcd", Edition.BOOK_MEDIUM
            )
        )
        eq_(expect_book_work, book.work)

        # The audiobook has been given the Work that will be used for
        # all audiobook-type LicensePools for that title going
        # forward.
        expect_audiobook_work, audiobook_work_is_new = (
            Work.open_access_for_permanent_work_id(
                self._db, "abcd", Edition.AUDIO_MEDIUM
            )
        )
        eq_(expect_audiobook_work, audiobook.work)

    def test_calculate_work_detaches_licensepool_with_no_title(self):
        # Here's a Work with an open-access edition of "abcd".
        work = self._work(with_license_pool=True)
        [book] = work.license_pools
        book.presentation_edition.permanent_work_id = "abcd"

        # But the LicensePool's presentation edition has lost its
        # title.
        book.presentation_edition.title = None

        # Calling calculate_work() on the LicensePool will detach the
        # book from its work, since a book with no title cannot have
        # an associated Work.
        work_after, is_new = book.calculate_work()
        eq_(None, work_after)
        eq_([], work.license_pools)

    def test_calculate_work_detaches_licensepool_with_no_pwid(self):
        # Here's a Work with an open-access edition of "abcd".
        work = self._work(with_license_pool=True)
        [book] = work.license_pools
        book.presentation_edition.permanent_work_id = "abcd"

        # Due to a earlier error, the Work also contains an edition
        # with no title or author, and thus no permanent work ID.
        edition, no_title = self._edition(with_license_pool=True)

        no_title.presentation_edition.title=None
        no_title.presentation_edition.author=None
        no_title.presentation_edition.permanent_work_id = None
        work.license_pools.append(no_title)

        # Calling calculate_work() on the functional LicensePool will
        # split off the bad one.
        work_after, is_new = book.calculate_work()
        eq_([book], work.license_pools)
        eq_(None, no_title.work)
        eq_(None, no_title.presentation_edition.work)

        # calculate_work() on the bad LicensePool will split it off from
        # the good one.
        work.license_pools.append(no_title)
        work_after_2, is_new = no_title.calculate_work()
        eq_(None, work_after_2)
        eq_([book], work.license_pools)

        # The same thing happens if the bad LicensePool has no
        # presentation edition at all.
        work.license_pools.append(no_title)
        no_title.presentation_edition = None
        work_after, is_new = book.calculate_work()
        eq_([book], work.license_pools)

        work.license_pools.append(no_title)
        work_after, is_new = no_title.calculate_work()
        eq_([book], work.license_pools)


    def test_pwids(self):
        """Test the property that finds all permanent work IDs
        associated with a Work.
        """
        # Create a (bad) situation in which LicensePools associated
        # with two different PWIDs are associated with the same work.
        work = self._work(with_license_pool=True)
        [lp1] = work.license_pools
        eq_(set([lp1.presentation_edition.permanent_work_id]),
            work.pwids)
        edition, lp2 = self._edition(with_license_pool=True)
        work.license_pools.append(lp2)

        # Work.pwids finds both PWIDs.
        eq_(set([lp1.presentation_edition.permanent_work_id,
                 lp2.presentation_edition.permanent_work_id]),
            work.pwids)

    def test_open_access_for_permanent_work_id_no_licensepools(self):
        eq_(
            (None, False), Work.open_access_for_permanent_work_id(
                self._db, "No such permanent work ID", Edition.BOOK_MEDIUM
            )
        )

    def test_open_access_for_permanent_work_id(self):
        # Two different works full of open-access license pools.
        w1 = self._work(with_license_pool=True, with_open_access_download=True)

        w2 = self._work(with_license_pool=True, with_open_access_download=True)

        [lp1] = w1.license_pools 
        [lp2] = w2.license_pools 

        # Work #2 has two different license pools grouped
        # together. Work #1 only has one.
        edition, lp3 = self._edition(
            with_license_pool=True, with_open_access_download=True
        )
        w2.license_pools.append(lp3)

        # Due to an error, it turns out both Works are providing the
        # exact same book.
        lp1.presentation_edition.permanent_work_id="abcd"
        lp2.presentation_edition.permanent_work_id="abcd"
        lp3.presentation_edition.permanent_work_id="abcd"

        # We've also got Work #3, which provides a commercial license
        # for that book.
        w3 = self._work(with_license_pool=True)
        w3_pool = w3.license_pools[0]
        w3_pool.presentation_edition.permanent_work_id="abcd"
        w3_pool.open_access = False

        # Work.open_access_for_permanent_work_id can resolve this problem.
        work, is_new = Work.open_access_for_permanent_work_id(
            self._db, "abcd", Edition.BOOK_MEDIUM
        )

        # Work #3 still exists and its license pool was not affected.
        eq_([w3], self._db.query(Work).filter(Work.id==w3.id).all())
        eq_(w3, w3_pool.work)

        # But the other three license pools now have the same work.
        eq_(work, lp1.work)
        eq_(work, lp2.work)
        eq_(work, lp3.work)
        
        # Because work #2 had two license pools, and work #1 only had
        # one, work #1 was merged into work #2, rather than the other
        # way around.
        eq_(w2, work)
        eq_(False, is_new)

        # Work #1 no longer exists.
        eq_([], self._db.query(Work).filter(Work.id==w1.id).all())

        # Calling Work.open_access_for_permanent_work_id again returns the same
        # result.
        eq_((w2, False), Work.open_access_for_permanent_work_id(
            self._db, "abcd", Edition.BOOK_MEDIUM
        ))

    def test_open_access_for_permanent_work_id_can_create_work(self):

        # Here's a LicensePool with no corresponding Work.
        edition, lp = self._edition(with_license_pool=True)
        edition.permanent_work_id="abcd"

        # open_access_for_permanent_work_id creates the Work.
        work, is_new = Work.open_access_for_permanent_work_id(
            self._db, "abcd", Edition.BOOK_MEDIUM
        )
        eq_([lp], work.license_pools)
        eq_(True, is_new)

    def test_make_exclusive_open_access_for_permanent_work_id(self):
        # Here's a work containing an open-access LicensePool for
        # literary work "abcd".
        work1 = self._work(with_license_pool=True, 
                          with_open_access_download=True)
        [abcd_oa] = work1.license_pools
        abcd_oa.presentation_edition.permanent_work_id="abcd"

        # Unfortunately, a commercial LicensePool for the literary
        # work "abcd" has gotten associated with the same work.
        edition, abcd_commercial = self._edition(
            with_license_pool=True, with_open_access_download=True
        )
        abcd_commercial.open_access = False
        abcd_commercial.presentation_edition.permanent_work_id="abcd"
        abcd_commercial.work = work1

        # Here's another Work containing an open-access LicensePool
        # for literary work "efgh".
        work2 = self._work(with_license_pool=True, 
                          with_open_access_download=True)
        [efgh_1] = work2.license_pools
        efgh_1.presentation_edition.permanent_work_id="efgh"

        # Unfortunately, there's another open-access LicensePool for
        # "efgh", and it's incorrectly associated with the "abcd"
        # work.
        edition, efgh_2 = self._edition(
            with_license_pool=True, with_open_access_download=True
        )
        efgh_2.presentation_edition.permanent_work_id = "efgh"
        efgh_2.work = work1

        # Let's fix these problems.
        work1.make_exclusive_open_access_for_permanent_work_id(
            "abcd", Edition.BOOK_MEDIUM
        )

        # The open-access "abcd" book is now the only LicensePool
        # associated with work1.
        eq_([abcd_oa], work1.license_pools)

        # Both open-access "efgh" books are now associated with work2.
        eq_(set([efgh_1, efgh_2]), set(work2.license_pools))

        # A third work has been created for the commercial edition of "abcd".
        assert abcd_commercial.work not in (work1, work2)

    def test_make_exclusive_open_access_for_null_permanent_work_id(self):
        # Here's a LicensePool that, due to a previous error, has
        # a null PWID in its presentation edition.
        work = self._work(with_license_pool=True, 
                          with_open_access_download=True)
        [null1] = work.license_pools
        null1.presentation_edition.title = None
        null1.presentation_edition.sort_author = None
        null1.presentation_edition.permanent_work_id = None
        
        # Here's another LicensePool associated with the same work and
        # with the same problem.
        edition, null2 = self._edition(
            with_license_pool=True, with_open_access_download=True
        )
        work.license_pools.append(null2)

        for pool in work.license_pools:
            pool.presentation_edition.title = None
            pool.presentation_edition.sort_author = None
            pool.presentation_edition.permanent_work_id = None

        work.make_exclusive_open_access_for_permanent_work_id(
            None, Edition.BOOK_MEDIUM
        )

        # Since a LicensePool with no PWID cannot have an associated Work,
        # this Work now have no LicensePools at all.
        eq_([], work.license_pools)

        eq_(None, null1.work)
        eq_(None, null2.work)

    def test_merge_into_success(self):
        # Here's a work with an open-access LicensePool.
        work1 = self._work(with_license_pool=True, 
                           with_open_access_download=True)
        [lp1] = work1.license_pools
        lp1.presentation_edition.permanent_work_id="abcd"

        # Let's give it a WorkGenre and a WorkCoverageRecord.
        genre, ignore = Genre.lookup(self._db, "Fantasy")
        wg, wg_is_new = get_one_or_create(
            self._db, WorkGenre, work=work1, genre=genre
        )
        wcr, wcr_is_new = WorkCoverageRecord.add_for(work1, "test")

        # Here's another work with an open-access LicensePool for the
        # same book.
        work2 = self._work(with_license_pool=True, 
                           with_open_access_download=True)
        [lp2] = work2.license_pools
        lp2.presentation_edition.permanent_work_id="abcd"

        # Let's merge the first work into the second.
        work1.merge_into(work2)

        # The first work has been deleted, as have its WorkGenre and
        # WorkCoverageRecord.
        eq_([], self._db.query(Work).filter(Work.id==work1.id).all())
        eq_([], self._db.query(WorkGenre).all())
        eq_([], self._db.query(WorkCoverageRecord).filter(
            WorkCoverageRecord.work_id==work1.id).all()
        )

    def test_open_access_for_permanent_work_id_fixes_mismatched_works_incidentally(self):

        # Here's a work with two open-access LicensePools for the book "abcd".
        work1 = self._work(with_license_pool=True, 
                           with_open_access_download=True)
        [abcd_1] = work1.license_pools
        edition, abcd_2 = self._edition(
            with_license_pool=True, with_open_access_download=True
        )
        work1.license_pools.append(abcd_2)

        # Unfortunately, due to an earlier error, that work also
        # contains a _third_ open-access LicensePool, and this one
        # belongs to a totally separate book, "efgh".
        edition, efgh = self._edition(
            with_license_pool=True, with_open_access_download=True
        )
        work1.license_pools.append(efgh)

        # Here's another work with an open-access LicensePool for the
        # book "abcd".
        work2 = self._work(with_license_pool=True, 
                           with_open_access_download=True)
        [abcd_3] = work2.license_pools

        # Unfortunately, this work also contains an open-access Licensepool 
        # for the totally separate book, 'ijkl".
        edition, ijkl = self._edition(
            with_license_pool=True, with_open_access_download=True
        )
        work2.license_pools.append(ijkl)

        # Mock the permanent work IDs for all the presentation
        # editions in play.
        def mock_pwid_abcd(debug=False):
            return "abcd"

        def mock_pwid_efgh(debug=False):
            return "efgh"

        def mock_pwid_ijkl(debug=False):
            return "ijkl"

        for lp in abcd_1, abcd_2, abcd_3:
            lp.presentation_edition.calculate_permanent_work_id = mock_pwid_abcd
            lp.presentation_edition.permanent_work_id = 'abcd'

        efgh.presentation_edition.calculate_permanent_work_id = mock_pwid_efgh
        efgh.presentation_edition.permanent_work_id = 'efgh'

        ijkl.presentation_edition.calculate_permanent_work_id = mock_pwid_ijkl
        ijkl.presentation_edition.permanent_work_id = 'ijkl'

        # Calling Work.open_access_for_permanent_work_id()
        # automatically kicks the 'efgh' and 'ijkl' LicensePools into
        # their own works, and merges the second 'abcd' work with the
        # first one. (The first work is chosen because it represents
        # two LicensePools for 'abcd', not just one.)
        abcd_work, abcd_new = Work.open_access_for_permanent_work_id(
            self._db, "abcd", Edition.BOOK_MEDIUM
        )
        efgh_work, efgh_new = Work.open_access_for_permanent_work_id(
            self._db, "efgh", Edition.BOOK_MEDIUM
        )
        ijkl_work, ijkl_new = Work.open_access_for_permanent_work_id(
            self._db, "ijkl", Edition.BOOK_MEDIUM
        )

        # We've got three different works here. The 'abcd' work is the
        # old 'abcd' work that had three LicensePools--the other work
        # was merged into it.
        eq_(abcd_1.work, abcd_work)
        assert efgh_work != abcd_work
        assert ijkl_work != abcd_work
        assert ijkl_work != efgh_work

        # The two 'new' works (for efgh and ijkl) are not counted as
        # new because they were created during the first call to
        # Work.open_access_for_permanent_work_id, when those
        # LicensePools were split out of Works where they didn't
        # belong.
        eq_(False, efgh_new)
        eq_(False, ijkl_new)

        eq_([ijkl], ijkl_work.license_pools)
        eq_([efgh], efgh_work.license_pools)
        eq_(3, len(abcd_work.license_pools))

    def test_open_access_for_permanent_work_id_avoids_infinite_loop(self):

        # Here's are three works for the books "abcd", "efgh", and "ijkl".
        abcd_work = self._work(with_license_pool=True, 
                               with_open_access_download=True)
        [abcd_1] = abcd_work.license_pools

        efgh_work = self._work(with_license_pool=True, 
                               with_open_access_download=True)
        [efgh_1] = efgh_work.license_pools

        # Unfortunately, due to an earlier error, the 'abcd' work
        # contains a LicensePool for 'efgh', and the 'efgh' work contains
        # a LicensePool for 'abcd'.
        #
        # (This is pretty much impossible, but bear with me...)

        edition, abcd_2 = self._edition(
            with_license_pool=True, with_open_access_download=True
        )
        efgh_work.license_pools.append(abcd_2)

        edition, efgh_2 = self._edition(
            with_license_pool=True, with_open_access_download=True
        )
        abcd_work.license_pools.append(efgh_2)

        def mock_pwid_abcd(debug=False):
            return "abcd"

        for lp in abcd_1, abcd_2:
            lp.presentation_edition.calculate_permanent_work_id = mock_pwid_abcd
            lp.presentation_edition.permanent_work_id = 'abcd'

        def mock_pwid_efgh(debug=False):
            return "efgh"

        for lp in efgh_1, efgh_2:
            lp.presentation_edition.calculate_permanent_work_id = mock_pwid_efgh
            lp.presentation_edition.permanent_work_id = 'efgh'

        # Calling Work.open_access_for_permanent_work_id() raises an
        # exception. We can't untangle the loop (for now) but at least
        # it doesn't put us into an infinite loop.
        assert_raises_regexp(
            ValueError,
            "Refusing to merge .* into .* because permanent work IDs don't match: abcd,efgh vs. abcd",
            Work.open_access_for_permanent_work_id, self._db, "abcd",
            Edition.BOOK_MEDIUM
        )

    def test_merge_into_raises_exception_if_grouping_rules_violated(self):
        # Here's a work with an open-access LicensePool.
        work1 = self._work(with_license_pool=True, 
                           with_open_access_download=True)
        [lp1] = work1.license_pools
        lp1.presentation_edition.permanent_work_id="abcd"

        # Here's another work with a commercial LicensePool for the
        # same book.
        work2 = self._work(with_license_pool=True, 
                           with_open_access_download=True)
        [lp2] = work2.license_pools
        lp2.open_access = False
        lp2.presentation_edition.permanent_work_id="abcd"

        # The works cannot be merged.
        assert_raises_regexp(
            ValueError, 
            "Refusing to merge .* into .* because it would put an open-access LicensePool into the same work as a non-open-access LicensePool.",
            work1.merge_into, work2,
        )

    def test_merge_into_raises_exception_if_pwids_differ(self):
        work1 = self._work(with_license_pool=True, 
                           with_open_access_download=True)
        [abcd_oa] = work1.license_pools
        abcd_oa.presentation_edition.permanent_work_id="abcd"

        work2 = self._work(with_license_pool=True, 
                           with_open_access_download=True)
        [efgh_oa] = work2.license_pools
        efgh_oa.presentation_edition.permanent_work_id="efgh"

        assert_raises_regexp(
            ValueError,
            "Refusing to merge .* into .* because permanent work IDs don't match: abcd vs. efgh",
            work1.merge_into, 
            work2
        )

    def test_licensepool_without_identifier_gets_no_work(self):
        work = self._work(with_license_pool=True)
        [lp] = work.license_pools
        lp.identifier = None

        # Even if the LicensePool had a work before, it gets removed.
        eq_((None, False), lp.calculate_work())
        eq_(None, lp.work)

    def test_licensepool_without_presentation_edition_gets_no_work(self):
        work = self._work(with_license_pool=True)
        [lp] = work.license_pools

        # This LicensePool has no presentation edition and no way of 
        # getting one.
        lp.presentation_edition = None
        lp.identifier.primarily_identifies = []

        # Even if the LicensePool had a work before, it gets removed.
        eq_((None, False), lp.calculate_work())
        eq_(None, lp.work)

class TestLoans(DatabaseTest):

    def test_open_access_loan(self):
        patron = self._patron()
        work = self._work(with_license_pool=True)
        pool = work.license_pools[0]
        pool.is_open_access = True

        # The patron has no active loans.
        eq_([], patron.loans)

        # Loan them the book
        fulfillment = pool.delivery_mechanisms[0]
        loan, was_new = pool.loan_to(patron, fulfillment=fulfillment)

        # Now they have a loan!
        eq_([loan], patron.loans)
        eq_(loan.patron, patron)
        eq_(loan.license_pool, pool)
        eq_(fulfillment, loan.fulfillment)
        assert (datetime.datetime.utcnow() - loan.start) < datetime.timedelta(seconds=1)

        # TODO: At some future point it may be relevant that loan.end
        # is None here, but before that happens the loan process will
        # become more complicated, so there's no point in writing
        # a bunch of test code now.

        # Try getting another loan for this book.
        loan2, was_new = pool.loan_to(patron)

        # They're the same!
        eq_(loan, loan2)
        eq_(False, was_new)


    def test_work(self):
        """Test the attribute that finds the Work for a Loan or Hold."""
        patron = self._patron()
        work = self._work(with_license_pool=True)
        pool = work.license_pools[0]

        # The easy cases.
        loan, is_new = pool.loan_to(patron)
        eq_(work, loan.work)

        loan.license_pool = None
        eq_(None, loan.work)

        # If pool.work is None but pool.edition.work is valid, we use that.
        loan.license_pool = pool
        pool.work = None
        # Presentation_edition is not representing a lendable object, 
        # but it is on a license pool, and a pool has lending capacity.  
        eq_(pool.presentation_edition.work, loan.work)

        # If that's also None, we're helpless.
        pool.presentation_edition.work = None
        eq_(None, loan.work)


class TestHold(DatabaseTest):

    def test_on_hold_to(self):
        now = datetime.datetime.utcnow()
        later = now + datetime.timedelta(days=1)
        patron = self._patron()
        edition = self._edition()
        pool = self._licensepool(edition)

        with temp_config() as config:
            config['policies'] = {
                Configuration.HOLD_POLICY : Configuration.HOLD_POLICY_ALLOW
            }
            hold, is_new = pool.on_hold_to(patron, now, later, 4)
            eq_(True, is_new)
            eq_(now, hold.start)
            eq_(None, hold.end)
            eq_(4, hold.position)

            # Now update the position to 0. It's the patron's turn
            # to check out the book.
            hold, is_new = pool.on_hold_to(patron, now, later, 0)
            eq_(False, is_new)
            eq_(now, hold.start)
            # The patron has until `hold.end` to actually check out the book.
            eq_(later, hold.end)
            eq_(0, hold.position)

    def test_work(self):
        # We don't need to test the functionality--that's tested in
        # Loan--just that Hold also has access to .work.
        patron = self._patron()
        work = self._work(with_license_pool=True)
        pool = work.license_pools[0]
        hold, is_new = pool.on_hold_to(patron)
        eq_(work, hold.work)

    def test_calculate_until(self):
        start = datetime.datetime(2010, 1, 1)

        # The cycle time is one week.
        default_loan = datetime.timedelta(days=6)
        default_reservation = datetime.timedelta(days=1)
        
        # I'm 20th in line for 4 books.
        #
        # After 6 days, four copies are released and I am 16th in line.
        # After 13 days, those copies are released and I am 12th in line.
        # After 20 days, those copies are released and I am 8th in line.
        # After 27 days, those copies are released and I am 4th in line.
        # After 34 days, those copies are released and get my notification.
        a = Hold._calculate_until(
            start, 20, 4, default_loan, default_reservation)
        eq_(a, start + datetime.timedelta(days=(7*5)-1))

        # If I am 21st in line, I need to wait six weeks.
        b = Hold._calculate_until(
            start, 21, 4, default_loan, default_reservation)
        eq_(b, start + datetime.timedelta(days=(7*6)-1))

        # If I am 3rd in line, I only need to wait six days--that's when
        # I'll get the notification message.
        b = Hold._calculate_until(
            start, 3, 4, default_loan, default_reservation)
        eq_(b, start + datetime.timedelta(days=6))

        # A new person gets the book every week. Someone has the book now
        # and there are 3 people ahead of me in the queue. I will get
        # the book in 6 days + 3 weeks
        c = Hold._calculate_until(
            start, 3, 1, default_loan, default_reservation)
        eq_(c, start + datetime.timedelta(days=(7*4)-1))

        # The book is reserved to me. I need to hurry up and check it out.
        d = Hold._calculate_until(
            start, 0, 1, default_loan, default_reservation)
        eq_(d, start + datetime.timedelta(days=1))

        # If there are no licenses, I will never get the book.
        e = Hold._calculate_until(
            start, 10, 0, default_loan, default_reservation)
        eq_(e, None)

class TestAnnotation(DatabaseTest):
    def test_set_inactive(self):
        pool = self._licensepool(None)
        annotation, ignore = create(
            self._db, Annotation,
            patron=self.default_patron,
            identifier=pool.identifier,
            motivation=Annotation.IDLING,
            content="The content",
            active=True,
        )
        yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
        annotation.timestamp = yesterday

        annotation.set_inactive()
        eq_(False, annotation.active)
        eq_(None, annotation.content)
        assert annotation.timestamp > yesterday

    def test_patron_annotations_are_descending(self):
        pool1 = self._licensepool(None)
        pool2 = self._licensepool(None)
        annotation1, ignore = create(
            self._db, Annotation,
            patron=self.default_patron,
            identifier=pool2.identifier,
            motivation=Annotation.IDLING,
            content="The content",
            active=True,
        )
        annotation2, ignore = create(
            self._db, Annotation,
            patron=self.default_patron,
            identifier=pool2.identifier,
            motivation=Annotation.IDLING,
            content="The content",
            active=True,
        )

        yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
        today = datetime.datetime.now()
        annotation1.timestamp = yesterday
        annotation2.timestamp = today

        eq_(2, len(self.default_patron.annotations))
        eq_(annotation2, self.default_patron.annotations[0])
        eq_(annotation1, self.default_patron.annotations[1])
    

class TestHyperlink(DatabaseTest):

    def test_add_link(self):
        edition, pool = self._edition(with_license_pool=True)
        identifier = edition.primary_identifier
        data_source = pool.data_source
        hyperlink, is_new = pool.add_link(
            Hyperlink.DESCRIPTION, "http://foo.com/", data_source, 
            "text/plain", "The content")
        eq_(True, is_new)
        rep = hyperlink.resource.representation
        eq_("text/plain", rep.media_type)
        eq_("The content", rep.content)
        eq_(Hyperlink.DESCRIPTION, hyperlink.rel)
        eq_(pool, hyperlink.license_pool)
        eq_(identifier, hyperlink.identifier)

    def test_add_link_fails_if_license_pool_and_identifier_dont_match(self):
        edition, pool = self._edition(with_license_pool=True)
        data_source = pool.data_source
        identifier = self._identifier()
        assert_raises_regexp(
            ValueError, re.compile("License pool is associated with .*, not .*!"),
            identifier.add_link,
            Hyperlink.DESCRIPTION, "http://foo.com/", data_source, 
            pool, "text/plain", "The content")

    def test_default_filename(self):
        m = Hyperlink._default_filename
        eq_("content", m(Hyperlink.OPEN_ACCESS_DOWNLOAD))
        eq_("cover", m(Hyperlink.IMAGE))
        eq_("cover-thumbnail", m(Hyperlink.THUMBNAIL_IMAGE))


class TestRepresentation(DatabaseTest):

    def test_normalized_content_path(self):
        eq_("baz", Representation.normalize_content_path(
            "/foo/bar/baz", "/foo/bar"))

        eq_("baz", Representation.normalize_content_path(
            "/foo/bar/baz", "/foo/bar/"))

        eq_("/foo/bar/baz", Representation.normalize_content_path(
            "/foo/bar/baz", "/blah/blah/"))

    def test_best_media_type(self):
        """Test our ability to determine whether the Content-Type
        header should override a presumed media type.
        """
        m = Representation._best_media_type

        # If there are no headers or no content-type header, the
        # presumed media type takes precedence.
        eq_("text/plain", m(None, "text/plain"))
        eq_("text/plain", m({}, "text/plain"))

        # Most of the time, the content-type header takes precedence over
        # the presumed media type.
        eq_("image/gif", m({"content-type": "image/gif"}, "text/plain"))

        # Except when the content-type header is so generic as to be uselses.
        eq_("text/plain", m(
            {"content-type": "application/octet-stream;profile=foo"}, 
            "text/plain")
        )

    def test_mirrorable_media_type(self):
        representation, ignore = self._representation(self._url)

        # Ebook formats and image formats get mirrored.
        representation.media_type = Representation.EPUB_MEDIA_TYPE
        eq_(True, representation.mirrorable_media_type)
        representation.media_type = Representation.MOBI_MEDIA_TYPE
        eq_(True, representation.mirrorable_media_type)
        representation.media_type = Representation.JPEG_MEDIA_TYPE
        eq_(True, representation.mirrorable_media_type)

        # Other media types don't get mirrored
        representation.media_type = "text/plain"
        eq_(False, representation.mirrorable_media_type)

    def test_external_media_type_and_extension(self):
        """Test the various transformations that might happen to media type
        and extension when we mirror a representation.
        """

        # A text file at /foo
        representation, ignore = self._representation(self._url, "text/plain")
        eq_("text/plain", representation.external_media_type)
        eq_('', representation.extension())

        # A JPEG at /foo.jpg
        representation, ignore = self._representation(
            self._url + ".jpg", "image/jpeg"
        )
        eq_("image/jpeg", representation.external_media_type)
        eq_(".jpg", representation.extension())

        # A JPEG at /foo
        representation, ignore = self._representation(self._url, "image/jpeg")
        eq_("image/jpeg", representation.external_media_type)
        eq_(".jpg", representation.extension())

        # A PNG at /foo
        representation, ignore = self._representation(self._url, "image/png")
        eq_("image/png", representation.external_media_type)
        eq_(".png", representation.extension())

        # An EPUB at /foo.epub.images -- information present in the URL
        # is preserved.
        representation, ignore = self._representation(
            self._url + '.epub.images', Representation.EPUB_MEDIA_TYPE
        )
        eq_(Representation.EPUB_MEDIA_TYPE, representation.external_media_type)
        eq_(".epub.images", representation.extension())
        

        # SVG representations are always converted to PNG on the way out.
        # This affects the media type.
        representation, ignore = self._representation(self._url + ".svg", "image/svg+xml")
        eq_("image/png", representation.external_media_type)
        eq_(".png", representation.extension())

    def test_set_fetched_content(self):
        representation, ignore = self._representation(self._url, "text/plain")
        representation.set_fetched_content("some text")
        eq_("some text", representation.content_fh().read())

    def test_set_fetched_content_file_on_disk(self):
        filename = "set_fetched_content_file_on_disk.txt"
        path = os.path.join(self.tmp_data_dir, filename)
        open(path, "w").write("some text")

        representation, ignore = self._representation(self._url, "text/plain")
        representation.set_fetched_content(None, filename)
        fh = representation.content_fh()
        eq_("some text", fh.read())

    def test_unicode_content_utf8_default(self):
        unicode_content = u"It’s complicated."

        utf8_content = unicode_content.encode("utf8")

        # This bytestring can be decoded as Windows-1252, but that
        # would be the wrong answer.
        bad_windows_1252 = utf8_content.decode("windows-1252")
        eq_(u"Itâ€™s complicated.", bad_windows_1252)

        representation, ignore = self._representation(self._url, "text/plain")
        representation.set_fetched_content(unicode_content, None)
        eq_(utf8_content, representation.content)

        # By trying to interpret the content as UTF-8 before falling back to 
        # Windows-1252, we get the right answer.
        eq_(unicode_content, representation.unicode_content)

    def test_unicode_content_windows_1252(self):
        unicode_content = u"A “love” story"
        windows_1252_content = unicode_content.encode("windows-1252")

        representation, ignore = self._representation(self._url, "text/plain")
        representation.set_fetched_content(windows_1252_content)
        eq_(windows_1252_content, representation.content)
        eq_(unicode_content, representation.unicode_content)

    def test_unicode_content_is_none_when_decoding_is_impossible(self):
        byte_content = b"\x81\x02\x03"
        representation, ignore = self._representation(self._url, "text/plain")
        representation.set_fetched_content(byte_content)
        eq_(byte_content, representation.content)
        eq_(None, representation.unicode_content)

    def test_presumed_media_type(self):
        h = DummyHTTPClient()

        # In the absence of a content-type header, the presumed_media_type
        # takes over.
        h.queue_response(200, None, content='content')
        representation, cached = Representation.get(
            self._db, 'http://url', do_get=h.do_get, max_age=0,
            presumed_media_type="text/xml"
        )
        eq_('text/xml', representation.media_type)

        # In the presence of a generic content-type header, the
        # presumed_media_type takes over.
        h.queue_response(200, 'application/octet-stream',
                         content='content')
        representation, cached = Representation.get(
            self._db, 'http://url', do_get=h.do_get, max_age=0,
            presumed_media_type="text/xml"
        )
        eq_('text/xml', representation.media_type)

        # A non-generic content-type header takes precedence over
        # presumed_media_type.
        h.queue_response(200, 'text/plain', content='content')
        representation, cached = Representation.get(
            self._db, 'http://url', do_get=h.do_get, max_age=0,
            presumed_media_type="text/xml"
        )
        eq_('text/plain', representation.media_type)


    def test_404_creates_cachable_representation(self):
        h = DummyHTTPClient()
        h.queue_response(404)

        url = self._url
        representation, cached = Representation.get(
            self._db, url, do_get=h.do_get)
        eq_(False, cached)

        representation2, cached = Representation.get(
            self._db, url, do_get=h.do_get)
        eq_(True, cached)
        eq_(representation, representation2)

    def test_302_creates_cachable_representation(self):
        h = DummyHTTPClient()
        h.queue_response(302)

        url = self._url
        representation, cached = Representation.get(
            self._db, url, do_get=h.do_get)
        eq_(False, cached)

        representation2, cached = Representation.get(
            self._db, url, do_get=h.do_get)
        eq_(True, cached)
        eq_(representation, representation2)

    def test_500_creates_uncachable_representation(self):
        h = DummyHTTPClient()
        h.queue_response(500)
        url = self._url
        representation, cached = Representation.get(
            self._db, url, do_get=h.do_get)
        eq_(False, cached)

        h.queue_response(500)
        representation, cached = Representation.get(
            self._db, url, do_get=h.do_get)
        eq_(False, cached)

    def test_response_reviewer_impacts_representation(self):
        h = DummyHTTPClient()
        h.queue_response(200, media_type='text/html')

        def reviewer(response):
            status, headers, content = response
            if 'html' in headers['content-type']:
                raise Exception("No. Just no.")

        representation, cached = Representation.get(
            self._db, self._url, do_get=h.do_get, response_reviewer=reviewer
        )
        assert "No. Just no." in representation.fetch_exception
        eq_(False, cached)

    def test_exception_handler(self):
        def oops(*args, **kwargs):
            raise Exception("oops!")

        # By default exceptions raised during get() are 
        # recorded along with the (empty) Representation objects
        representation, cached = Representation.get(
            self._db, self._url, do_get=oops,
        )
        assert representation.fetch_exception.strip().endswith(
            "Exception: oops!"
        )
        eq_(None, representation.content)
        eq_(None, representation.status_code)

        # But we can ask that exceptions simply be re-raised instead of
        # being handled.
        assert_raises_regexp(
            Exception, "oops!", Representation.get,
            self._db, self._url, do_get=oops,
            exception_handler=Representation.reraise_exception
        )

    def test_url_extension(self):
        epub, ignore = self._representation("test.epub")
        eq_(".epub", epub.url_extension)

        epub3, ignore = self._representation("test.epub3")
        eq_(".epub3", epub3.url_extension)

        noimages, ignore = self._representation("test.epub.noimages")
        eq_(".epub.noimages", noimages.url_extension)

        unknown, ignore = self._representation("test.1234.abcd")
        eq_(".abcd", unknown.url_extension)

        no_extension, ignore = self._representation("test")
        eq_(None, no_extension.url_extension)

        no_filename, ignore = self._representation("foo.com/")
        eq_(None, no_filename.url_extension)

        query_param, ignore = self._representation("test.epub?version=3")
        eq_(".epub", query_param.url_extension)

    def test_clean_media_type(self):
        m = Representation._clean_media_type
        eq_("image/jpeg", m("image/jpeg"))
        eq_("application/atom+xml",
            m("application/atom+xml;profile=opds-catalog;kind=acquisition")
        )

    def test_extension(self):
        m = Representation._extension
        eq_(".jpg", m("image/jpeg"))
        eq_(".mobi", m("application/x-mobipocket-ebook"))
        eq_("", m("no/such-media-type"))

    def test_default_filename(self):

        # Here's a common sort of URL.
        url = "http://example.com/foo/bar/baz.txt"
        representation, ignore = self._representation(url)

        # Here's the filename we would give it if we were to mirror
        # it.
        filename = representation.default_filename()
        eq_("baz.txt", filename)

        # File extension is always set based on media type.
        filename = representation.default_filename(destination_type="image/png")
        eq_("baz.png", filename)

        # The original file extension is not treated as reliable and
        # need not be present.
        url = "http://example.com/1"
        representation, ignore = self._representation(url, "text/plain")
        filename = representation.default_filename()
        eq_("1", filename)

        # Again, file extension is always set based on media type.
        filename = representation.default_filename(destination_type="image/png")
        eq_("1.png", filename)

        # In this case, don't have an extension registered for
        # text/plain, so the extension is omitted.
        filename = representation.default_filename(destination_type="text/plain")
        eq_("1", filename)

        # This URL has no path component, so we can't even come up with a
        # decent default filename. We have to go with 'resource'.
        representation, ignore = self._representation("http://example.com/", "text/plain")
        eq_('resource', representation.default_filename())
        eq_('resource.png', representation.default_filename(destination_type="image/png"))

        # But if we know what type of thing we're linking to, we can
        # do a little better.
        link = Hyperlink(rel=Hyperlink.IMAGE)
        filename = representation.default_filename(link=link)
        eq_('cover', filename)
        filename = representation.default_filename(link=link, destination_type="image/png")
        eq_('cover.png', filename)

    def test_automatic_conversion_svg_to_png(self):
        svg = """<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN"
  "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">

<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50">
    <ellipse cx="50" cy="25" rx="50" ry="25" style="fill:blue;"/>
</svg>"""
        edition = self._edition()
        pool = self._licensepool(edition)
        source = DataSource.lookup(self._db, DataSource.OVERDRIVE)
        hyperlink, ignore = pool.add_link(
            Hyperlink.IMAGE, None, source, Representation.SVG_MEDIA_TYPE,
            content=svg)
        representation = hyperlink.resource.representation

        eq_(Representation.SVG_MEDIA_TYPE, representation.media_type)
        eq_(Representation.PNG_MEDIA_TYPE, representation.external_media_type)

        # If we get the Representation as a PIL image, it's automatically
        # converted to PNG.
        image = representation.as_image()
        eq_("PNG", image.format)
        expect = StringIO()
        image.save(expect, format='PNG')

        # When we prepare to mirror the Representation to an external
        # file store, it's automatically converted to PNG.
        external_fh = representation.external_content()
        eq_(expect.getvalue(), external_fh.read())
        eq_(Representation.PNG_MEDIA_TYPE, representation.external_media_type)
        
        # Verify that the conversion happened correctly.

        # Even though the SVG image is smaller than the thumbnail
        # size, thumbnailing it will create a separate PNG-format
        # Representation, because we want all the thumbnails to be
        # bitmaps.
        thumbnail, is_new = representation.scale(
            Edition.MAX_THUMBNAIL_HEIGHT, Edition.MAX_THUMBNAIL_WIDTH,
            self._url, Representation.PNG_MEDIA_TYPE
        )
        eq_(True, is_new)
        assert thumbnail != hyperlink.resource.representation
        eq_(Representation.PNG_MEDIA_TYPE, thumbnail.media_type)


class TestCoverResource(DatabaseTest):

    def sample_cover_path(self, name):
        base_path = os.path.split(__file__)[0]
        resource_path = os.path.join(base_path, "files", "covers")
        sample_cover_path = os.path.join(resource_path, name)
        return sample_cover_path

    def sample_cover_representation(self, name):
        sample_cover_path = self.sample_cover_path(name)
        return self._representation(
            media_type="image/png", content=open(sample_cover_path).read())[0]

    def test_set_cover(self):
        edition, pool = self._edition(with_license_pool=True)
        original = self._url
        mirror = self._url
        thumbnail_mirror = self._url
        sample_cover_path = self.sample_cover_path("test-book-cover.png")
        hyperlink, ignore = pool.add_link(
            Hyperlink.IMAGE, original, edition.data_source, "image/png",
            content=open(sample_cover_path).read())
        full_rep = hyperlink.resource.representation
        full_rep.mirror_url = mirror
        full_rep.set_as_mirrored()

        edition.set_cover(hyperlink.resource)
        eq_(mirror, edition.cover_full_url)
        eq_(None, edition.cover_thumbnail_url)

        # Now scale the cover.
        thumbnail, ignore = self._representation()
        thumbnail.thumbnail_of = full_rep
        thumbnail.mirror_url = thumbnail_mirror
        thumbnail.set_as_mirrored()
        edition.set_cover(hyperlink.resource)
        eq_(mirror, edition.cover_full_url)
        eq_(thumbnail_mirror, edition.cover_thumbnail_url)

    def test_set_cover_for_very_small_image(self):
        edition, pool = self._edition(with_license_pool=True)
        original = self._url
        mirror = self._url
        sample_cover_path = self.sample_cover_path("tiny-image-cover.png")
        hyperlink, ignore = pool.add_link(
            Hyperlink.IMAGE, original, edition.data_source, "image/png",
            open(sample_cover_path).read())
        full_rep = hyperlink.resource.representation
        full_rep.mirror_url = mirror
        full_rep.set_as_mirrored()

        edition.set_cover(hyperlink.resource)
        eq_(mirror, edition.cover_full_url)
        eq_(mirror, edition.cover_thumbnail_url)

    def test_attempt_to_scale_non_image_sets_scale_exception(self):
        rep, ignore = self._representation(media_type="text/plain", content="foo")
        scaled, ignore = rep.scale(300, 600, self._url, "image/png")
        expect = "ValueError: Cannot load non-image representation as image: type text/plain"
        assert scaled == rep
        assert expect in rep.scale_exception
        
    def test_cannot_scale_to_non_image(self):
        rep, ignore = self._representation(media_type="image/png", content="foo")
        assert_raises_regexp(
            ValueError, 
            "Unsupported destination media type: text/plain",
            rep.scale, 300, 600, self._url, "text/plain")
        

    def test_success(self):
        cover = self.sample_cover_representation("test-book-cover.png")
        url = self._url
        thumbnail, is_new = cover.scale(300, 600, url, "image/png")
        eq_(True, is_new)
        eq_(url, thumbnail.url)
        eq_(url, thumbnail.mirror_url)
        eq_(None, thumbnail.mirrored_at)
        eq_(cover, thumbnail.thumbnail_of)
        eq_("image/png", thumbnail.media_type)
        eq_(300, thumbnail.image_height)
        eq_(200, thumbnail.image_width)

        # Try to scale the image to the same URL, and nothing will
        # happen, even though the proposed image size is
        # different.
        thumbnail2, is_new = cover.scale(400, 700, url, "image/png")
        eq_(thumbnail2, thumbnail)
        eq_(False, is_new)

        # Let's say the thumbnail has been mirrored.
        thumbnail.mirrored_at = datetime.datetime.utcnow()

        old_content = thumbnail.content
        # With the force argument we can forcibly re-scale an image,
        # changing its size.
        eq_([thumbnail], cover.thumbnails)
        thumbnail2, is_new = cover.scale(
            400, 700, url, "image/png", force=True)
        eq_(True, is_new)
        eq_([thumbnail2], cover.thumbnails)
        eq_(cover, thumbnail2.thumbnail_of)

        # The same Representation, but now its data is different.
        eq_(thumbnail, thumbnail2)
        assert thumbnail2.content != old_content
        eq_(400, thumbnail.image_height)
        eq_(266, thumbnail.image_width)

        # The thumbnail has been regenerated, so it needs to be mirrored again.
        eq_(None, thumbnail.mirrored_at)

    def test_book_with_odd_aspect_ratio(self):
        # This book is 1200x600.
        cover = self.sample_cover_representation("childrens-book-cover.png")
        url = self._url
        thumbnail, is_new = cover.scale(300, 400, url, "image/png")
        eq_(True, is_new)
        eq_(url, thumbnail.url)
        eq_(cover, thumbnail.thumbnail_of)
        # The width was reduced to max_width, a reduction of a factor of three
        eq_(400, thumbnail.image_width)
        # The height was also reduced by a factory of three, even
        # though this takes it below max_height.
        eq_(200, thumbnail.image_height)

    def test_book_smaller_than_thumbnail_size(self):
        # This book is 200x200. No thumbnail will be created.
        cover = self.sample_cover_representation("tiny-image-cover.png")
        url = self._url
        thumbnail, is_new = cover.scale(300, 600, url, "image/png")
        eq_(False, is_new)
        eq_(thumbnail, cover)
        eq_([], cover.thumbnails)
        eq_(None, thumbnail.thumbnail_of)
        assert thumbnail.url != url

    def test_best_covers_among(self):
        # Here's a book with a thumbnail image.
        edition, pool = self._edition(with_license_pool=True)

        link1, ignore = pool.add_link(
            Hyperlink.THUMBNAIL_IMAGE, self._url, pool.data_source
        )
        resource_with_no_representation = link1.resource

        # A resource with no representation is not considered even if
        # it's the only option.
        eq_([], Resource.best_covers_among([resource_with_no_representation]))

        # Here's an abysmally bad cover.
        lousy_cover = self.sample_cover_representation("tiny-image-cover.png")
        lousy_cover.image_height=1
        lousy_cover.image_width=10000 
        link2, ignore = pool.add_link(
            Hyperlink.THUMBNAIL_IMAGE, self._url, pool.data_source
        )
        resource_with_lousy_cover = link2.resource
        resource_with_lousy_cover.representation = lousy_cover

        # This cover is so bad that it's not even considered if it's
        # the only option.
        eq_([], Resource.best_covers_among([resource_with_lousy_cover]))

        # Here's a decent cover.
        decent_cover = self.sample_cover_representation("test-book-cover.png")
        link3, ignore = pool.add_link(
            Hyperlink.THUMBNAIL_IMAGE, self._url, pool.data_source
        )
        resource_with_decent_cover = link3.resource
        resource_with_decent_cover.representation = decent_cover

        # This cover is at least good enough to pass muster if there
        # is no other option.
        eq_(
            [resource_with_decent_cover], 
            Resource.best_covers_among([resource_with_decent_cover])
        )

        # Let's create another cover image with identical
        # characteristics.
        link4, ignore = pool.add_link(
            Hyperlink.THUMBNAIL_IMAGE, self._url, pool.data_source
        )
        resource_with_decent_cover_2 = link4.resource
        resource_with_decent_cover_2.representation = decent_cover
        l = [resource_with_decent_cover, resource_with_decent_cover_2]

        # best_covers_among() can't decide between the two -- they have
        # the same score.
        eq_(set(l), set(Resource.best_covers_among(l)))

        # But if we give one of them a bump by saying it's the one the
        # metadata wrangler said to use...
        metadata_wrangler = DataSource.lookup(
            self._db, DataSource.METADATA_WRANGLER
        )
        resource_with_decent_cover.data_source = metadata_wrangler

        # ...the decision becomes easy.
        eq_([resource_with_decent_cover], Resource.best_covers_among(l))


    def test_quality_as_thumbnail_image(self):

        # Get some data sources ready, since a big part of image
        # quality comes from data source.
        gutenberg = DataSource.lookup(self._db, DataSource.GUTENBERG)
        gutenberg_cover_generator = DataSource.lookup(
            self._db, DataSource.GUTENBERG_COVER_GENERATOR
        )
        overdrive = DataSource.lookup(self._db, DataSource.OVERDRIVE)
        metadata_wrangler = DataSource.lookup(
            self._db, DataSource.METADATA_WRANGLER
        )

        # Here's a book with a thumbnail image.
        edition, pool = self._edition(with_license_pool=True)
        hyperlink, ignore = pool.add_link(
            Hyperlink.THUMBNAIL_IMAGE, self._url, overdrive
        )
        resource = hyperlink.resource
        
        # Without a representation, the thumbnail image is useless.
        eq_(0, resource.quality_as_thumbnail_image)

        ideal_height = Identifier.IDEAL_IMAGE_HEIGHT
        ideal_width = Identifier.IDEAL_IMAGE_WIDTH

        cover = self.sample_cover_representation("tiny-image-cover.png")
        resource.representation = cover
        eq_(1.0, resource.quality_as_thumbnail_image)

        # Changing the image aspect ratio affects the quality as per
        # thumbnail_size_quality_penalty.
        cover.image_height = ideal_height * 2
        cover.image_width = ideal_width
        eq_(0.5, resource.quality_as_thumbnail_image)
        
        # Changing the data source also affects the quality. Gutenberg
        # covers are penalized heavily...
        cover.image_height = ideal_height
        cover.image_width = ideal_width
        resource.data_source = gutenberg
        eq_(0.5, resource.quality_as_thumbnail_image)

        # The Gutenberg cover generator is penalized less heavily.
        resource.data_source = gutenberg_cover_generator
        eq_(0.6, resource.quality_as_thumbnail_image)

        # The metadata wrangler actually gets a _bonus_, to encourage the
        # use of its covers over those provided by license sources.
        resource.data_source = metadata_wrangler
        eq_(2, resource.quality_as_thumbnail_image)
        

    def test_thumbnail_size_quality_penalty(self):
        """Verify that Representation._cover_size_quality_penalty penalizes
        images that are the wrong aspect ratio, or too small.
        """

        ideal_ratio = Identifier.IDEAL_COVER_ASPECT_RATIO
        ideal_height = Identifier.IDEAL_IMAGE_HEIGHT
        ideal_width = Identifier.IDEAL_IMAGE_WIDTH

        def f(width, height):
            return Representation._thumbnail_size_quality_penalty(width, height)

        # In the absence of any size information we assume
        # everything's fine.
        eq_(1, f(None, None))

        # The perfect image has no penalty.
        eq_(1, f(ideal_width, ideal_height))

        # An image that is the perfect aspect ratio, but too large,
        # has no penalty.
        eq_(1, f(ideal_width*2, ideal_height*2))
        
        # An image that is the perfect aspect ratio, but is too small,
        # is penalised.
        eq_(1/4.0, f(ideal_width*0.5, ideal_height*0.5))
        eq_(1/16.0, f(ideal_width*0.25, ideal_height*0.25))

        # An image that deviates from the perfect aspect ratio is
        # penalized in proportion.
        eq_(1/2.0, f(ideal_width*2, ideal_height))
        eq_(1/2.0, f(ideal_width, ideal_height*2))
        eq_(1/4.0, f(ideal_width*4, ideal_height))
        eq_(1/4.0, f(ideal_width, ideal_height*4))


class TestDeliveryMechanism(DatabaseTest):

    def test_default_fulfillable(self):
        mechanism, is_new = DeliveryMechanism.lookup(
            self._db, Representation.EPUB_MEDIA_TYPE, 
            DeliveryMechanism.ADOBE_DRM
        )
        eq_(False, is_new)
        eq_(True, mechanism.default_client_can_fulfill)

        mechanism, is_new = DeliveryMechanism.lookup(
            self._db, Representation.PDF_MEDIA_TYPE, 
            DeliveryMechanism.STREAMING_DRM
        )
        eq_(True, is_new)
        eq_(False, mechanism.default_client_can_fulfill)

    def test_association_with_licensepool(self):
        ignore, with_download = self._edition(with_open_access_download=True)
        [lpmech] = with_download.delivery_mechanisms
        eq_("Dummy content", lpmech.resource.representation.content)
        mech = lpmech.delivery_mechanism
        eq_(Representation.EPUB_MEDIA_TYPE, mech.content_type)
        eq_(mech.NO_DRM, mech.drm_scheme)


class TestRightsStatus(DatabaseTest):

    def test_lookup(self):
        status = RightsStatus.lookup(self._db, RightsStatus.IN_COPYRIGHT)
        eq_(RightsStatus.IN_COPYRIGHT, status.uri)
        eq_(RightsStatus.NAMES.get(RightsStatus.IN_COPYRIGHT), status.name)
        
        status = RightsStatus.lookup(self._db, RightsStatus.CC0)
        eq_(RightsStatus.CC0, status.uri)
        eq_(RightsStatus.NAMES.get(RightsStatus.CC0), status.name)
        
        status = RightsStatus.lookup(self._db, "not a known rights uri")
        eq_(RightsStatus.UNKNOWN, status.uri)
        eq_(RightsStatus.NAMES.get(RightsStatus.UNKNOWN), status.name)


class TestCredentials(DatabaseTest):
    
    def test_temporary_token(self):

        # Create a temporary token good for one hour.
        duration = datetime.timedelta(hours=1)
        data_source = DataSource.lookup(self._db, DataSource.ADOBE)
        patron = self._patron()
        now = datetime.datetime.utcnow() 
        expect_expires = now + duration
        token, is_new = Credential.temporary_token_create(
            self._db, data_source, "some random type", patron, duration)
        eq_(data_source, token.data_source)
        eq_("some random type", token.type)
        eq_(patron, token.patron)
        expires_difference = abs((token.expires-expect_expires).seconds)
        assert expires_difference < 2

        # Now try to look up the credential based solely on the UUID.
        new_token = Credential.lookup_by_token(
            self._db, data_source, token.type, token.credential)
        eq_(new_token, token)

        # When we call lookup_by_temporary_token, the token is automatically
        # expired and we cannot use it anymore.
        new_token = Credential.lookup_by_temporary_token(
            self._db, data_source, token.type, token.credential)
        eq_(new_token, token)        
        assert new_token.expires < now

        new_token = Credential.lookup_by_token(
            self._db, data_source, token.type, token.credential)
        eq_(None, new_token)

        new_token = Credential.lookup_by_temporary_token(
            self._db, data_source, token.type, token.credential)
        eq_(None, new_token)
 
        # A token with no expiration date is treated as expired...
        token.expires = None
        self._db.commit()
        no_expiration_token = Credential.lookup_by_token(
            self._db, data_source, token.type, token.credential)
        eq_(None, no_expiration_token)

        # ...unless we specifically say we're looking for a persistent token.
        no_expiration_token = Credential.lookup_by_token(
            self._db, data_source, token.type, token.credential, 
            allow_persistent_token=True
        )
        eq_(token, no_expiration_token)

    def test_temporary_token_overwrites_old_token(self):
        duration = datetime.timedelta(hours=1)
        data_source = DataSource.lookup(self._db, DataSource.ADOBE)
        patron = self._patron()
        old_token, is_new = Credential.temporary_token_create(
            self._db, data_source, "some random type", patron, duration)
        eq_(True, is_new)
        old_credential = old_token.credential

        # Creating a second temporary token overwrites the first.
        token, is_new = Credential.temporary_token_create(
            self._db, data_source, "some random type", patron, duration)
        eq_(False, is_new)
        eq_(token.id, old_token.id)
        assert old_credential != token.credential

    def test_persistent_token(self):

        # Create a persistent token.
        data_source = DataSource.lookup(self._db, DataSource.ADOBE)
        patron = self._patron()
        token, is_new = Credential.persistent_token_create(
            self._db, data_source, "some random type", patron
        )
        eq_(data_source, token.data_source)
        eq_("some random type", token.type)
        eq_(patron, token.patron)

        # Now try to look up the credential based solely on the UUID.
        new_token = Credential.lookup_by_token(
            self._db, data_source, token.type, token.credential, 
            allow_persistent_token=True
        )
        eq_(new_token, token)
        credential = new_token.credential

        # We can keep calling lookup_by_token and getting the same
        # Credential object with the same .credential -- it doesn't
        # expire.
        again_token = Credential.lookup_by_token(
            self._db, data_source, token.type, token.credential, 
            allow_persistent_token=True
        )
        eq_(again_token, new_token)
        eq_(again_token.credential, credential)

    def test_cannot_look_up_nonexistent_token(self):
        data_source = DataSource.lookup(self._db, DataSource.ADOBE)
        new_token = Credential.lookup_by_token(
            self._db, data_source, "no such type", "no such credential")
        eq_(None, new_token)

class TestPatron(DatabaseTest):

    def test_external_type_regular_expression(self):
        patron = self._patron("234")
        patron.authorization_identifier = "A123"
        key = Patron.EXTERNAL_TYPE_REGULAR_EXPRESSION
        with temp_config() as config:

            config[Configuration.POLICIES] = {}

            config[Configuration.POLICIES][key] = None
            eq_(None, patron.external_type)

            config[Configuration.POLICIES][key] = "([A-Z])"
            eq_("A", patron.external_type)
            patron._external_type = None

            config[Configuration.POLICIES][key] = "([0-9]$)"
            eq_("3", patron.external_type)
            patron._external_type = None

            config[Configuration.POLICIES][key] = "A"
            eq_(None, patron.external_type)
            patron._external_type = None

            config[Configuration.POLICIES][key] = "(not a valid regexp"
            assert_raises(TypeError, lambda x: patron.external_type)
            patron._external_type = None


class TestBaseCoverageRecord(DatabaseTest):

    def test_not_covered(self):
        source = DataSource.lookup(self._db, DataSource.OCLC)

        # Here are four identifiers with four relationships to a
        # certain coverage provider: no coverage at all, successful
        # coverage, a transient failure and a permanent failure.

        no_coverage = self._identifier()

        success = self._identifier()
        success_record = self._coverage_record(success, source)
        success_record.timestamp = (
            datetime.datetime.now() - datetime.timedelta(seconds=3600)
        )
        eq_(CoverageRecord.SUCCESS, success_record.status)

        transient = self._identifier()
        transient_record = self._coverage_record(
            transient, source, status=CoverageRecord.TRANSIENT_FAILURE
        )
        eq_(CoverageRecord.TRANSIENT_FAILURE, transient_record.status)

        persistent = self._identifier()
        persistent_record = self._coverage_record(
            persistent, source, status = BaseCoverageRecord.PERSISTENT_FAILURE
        )
        eq_(CoverageRecord.PERSISTENT_FAILURE, persistent_record.status)
        
        # Here's a query that finds all four.
        qu = self._db.query(Identifier).outerjoin(CoverageRecord)
        eq_(4, qu.count())

        def check_not_covered(expect, **kwargs):
            missing = CoverageRecord.not_covered(**kwargs)
            eq_(sorted(expect), sorted(qu.filter(missing).all()))

        # By default, not_covered() only finds the identifier with no
        # coverage and the one with a transient failure.
        check_not_covered([no_coverage, transient])

        # If we pass in different values for covered_status, we change what
        # counts as 'coverage'. In this case, we allow transient failures
        # to count as 'coverage'.
        check_not_covered(
            [no_coverage],
            count_as_covered=[CoverageRecord.PERSISTENT_FAILURE, 
                              CoverageRecord.TRANSIENT_FAILURE,
                              CoverageRecord.SUCCESS]
        )

        # Here, only success counts as 'coverage'.
        check_not_covered(
            [no_coverage, transient, persistent],
            count_as_covered=CoverageRecord.SUCCESS
        )

        # We can also say that coverage doesn't count if it was achieved before
        # a certain time. Here, we'll show that passing in the timestamp
        # of the 'success' record means that record still counts as covered.
        check_not_covered(
            [no_coverage, transient],
            count_as_not_covered_if_covered_before=success_record.timestamp
        )

        # But if we pass in a time one second later, the 'success'
        # record no longer counts as covered.
        one_second_after = (
            success_record.timestamp + datetime.timedelta(seconds=1)
        )
        check_not_covered(
            [success, no_coverage, transient],
            count_as_not_covered_if_covered_before=one_second_after
        )        


class TestCoverageRecord(DatabaseTest):

    def test_lookup(self):
        source = DataSource.lookup(self._db, DataSource.OCLC)
        edition = self._edition()
        operation = 'foo'
        record = self._coverage_record(edition, source, operation)

        lookup = CoverageRecord.lookup(edition, source, operation)
        eq_(lookup, record)

        lookup = CoverageRecord.lookup(edition, source)
        eq_(None, lookup)

        lookup = CoverageRecord.lookup(edition.primary_identifier, source, operation)
        eq_(lookup, record)

        lookup = CoverageRecord.lookup(edition.primary_identifier, source)
        eq_(None, lookup)

    def test_add_for(self):
        source = DataSource.lookup(self._db, DataSource.OCLC)
        edition = self._edition()
        operation = 'foo'
        record, is_new = CoverageRecord.add_for(edition, source, operation)
        eq_(True, is_new)

        # If we call add_for again we get the same record back, but we
        # can modify the timestamp.
        a_week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
        record2, is_new = CoverageRecord.add_for(
            edition, source, operation, a_week_ago
        )
        eq_(record, record2)
        eq_(False, is_new)
        eq_(a_week_ago, record2.timestamp)

        # If we don't specify an operation we get a totally different
        # record.
        record3, ignore = CoverageRecord.add_for(edition, source)
        assert record3 != record
        eq_(None, record3.operation)
        seconds = (datetime.datetime.utcnow() - record3.timestamp).seconds
        assert seconds < 10

        # If we call lookup we get the same record.
        record4 = CoverageRecord.lookup(edition.primary_identifier, source)
        eq_(record3, record4)

        # We can change the status.
        record5, is_new = CoverageRecord.add_for(
            edition, source, operation, 
            status=CoverageRecord.PERSISTENT_FAILURE
        )
        eq_(record5, record)
        eq_(CoverageRecord.PERSISTENT_FAILURE, record.status)

class TestWorkCoverageRecord(DatabaseTest):

    def test_lookup(self):
        work = self._work()
        operation = 'foo'

        lookup = WorkCoverageRecord.lookup(work, operation)
        eq_(None, lookup)

        record = self._work_coverage_record(work, operation)

        lookup = WorkCoverageRecord.lookup(work, operation)
        eq_(lookup, record)

        eq_(None, WorkCoverageRecord.lookup(work, "another operation"))

    def test_add_for(self):
        work = self._work()
        operation = 'foo'
        record, is_new = WorkCoverageRecord.add_for(work, operation)
        eq_(True, is_new)

        # If we call add_for again we get the same record back, but we
        # can modify the timestamp.
        a_week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
        record2, is_new = WorkCoverageRecord.add_for(
            work, operation, a_week_ago
        )
        eq_(record, record2)
        eq_(False, is_new)
        eq_(a_week_ago, record2.timestamp)

        # If we don't specify an operation we get a totally different
        # record.
        record3, ignore = WorkCoverageRecord.add_for(work, None)
        assert record3 != record
        eq_(None, record3.operation)
        seconds = (datetime.datetime.utcnow() - record3.timestamp).seconds
        assert seconds < 10

        # If we call lookup we get the same record.
        record4 = WorkCoverageRecord.lookup(work, None)
        eq_(record3, record4)

        # We can change the status.
        record5, is_new = WorkCoverageRecord.add_for(
            work, operation, status=WorkCoverageRecord.PERSISTENT_FAILURE
        )
        eq_(record5, record)
        eq_(WorkCoverageRecord.PERSISTENT_FAILURE, record.status)

class TestComplaint(DatabaseTest):

    def setup(self):
        super(TestComplaint, self).setup()
        self.edition, self.pool = self._edition(with_license_pool=True)
        self.type = "http://librarysimplified.org/terms/problem/wrong-genre"

    def test_success(self):
        complaint, is_new = Complaint.register(
            self.pool, self.type, "foo", "bar"
        )
        eq_(True, is_new)
        eq_(self.type, complaint.type)
        eq_("foo", complaint.source)
        eq_("bar", complaint.detail)
        assert abs(datetime.datetime.utcnow() -complaint.timestamp).seconds < 3

        # A second complaint from the same source is folded into the
        # original complaint.
        complaint2, is_new = Complaint.register(
            self.pool, self.type, "foo", "baz"
        )
        eq_(False, is_new)
        eq_(complaint.id, complaint2.id)
        eq_("baz", complaint.detail)

        eq_(1, len(self.pool.complaints))

    def test_success_no_source(self):
        complaint, is_new = Complaint.register(
            self.pool, self.type, None, None
        )
        eq_(True, is_new)
        eq_(self.type, complaint.type)
        eq_(None, complaint.source)

        # A second identical complaint from no source is treated as a
        # separate complaint.
        complaint2, is_new = Complaint.register(
            self.pool, self.type, None, None
        )
        eq_(True, is_new)
        eq_(None, complaint.source)
        assert complaint2.id != complaint.id

        eq_(2, len(self.pool.complaints))

    def test_failure_no_licensepool(self):
        assert_raises(
            ValueError, Complaint.register, self.pool, type, None, None
        )

    def test_unrecognized_type(self):
        type = "http://librarysimplified.org/terms/problem/no-such-error"
        assert_raises(
            ValueError, Complaint.register, self.pool, type, None, None
        )
        
    def test_register_resolved(self):
        complaint, is_new = Complaint.register(
            self.pool, self.type, "foo", "bar", resolved=datetime.datetime.utcnow()
        )
        eq_(True, is_new)
        eq_(self.type, complaint.type)
        eq_("foo", complaint.source)
        eq_("bar", complaint.detail)
        assert abs(datetime.datetime.utcnow() -complaint.timestamp).seconds < 3
        assert abs(datetime.datetime.utcnow() -complaint.resolved).seconds < 3

        # A second complaint from the same source is not folded into the same complaint.
        complaint2, is_new = Complaint.register(
            self.pool, self.type, "foo", "baz"
        )
        eq_(True, is_new)
        assert complaint2.id != complaint.id
        eq_("baz", complaint2.detail)
        eq_(2, len(self.pool.complaints))

    def test_resolve(self):
        complaint, is_new = Complaint.register(
            self.pool, self.type, "foo", "bar"
        )
        complaint.resolve()
        assert complaint.resolved != None
        assert abs(datetime.datetime.utcnow() - complaint.resolved).seconds < 3


class TestDeliveryMechanism(DatabaseTest):

    def setup(self):
        super(TestDeliveryMechanism, self).setup()
        self.epub_no_drm, ignore = DeliveryMechanism.lookup(
            self._db, Representation.EPUB_MEDIA_TYPE, DeliveryMechanism.NO_DRM)
        self.epub_adobe_drm, ignore = DeliveryMechanism.lookup(
            self._db, Representation.EPUB_MEDIA_TYPE, DeliveryMechanism.ADOBE_DRM)
        self.overdrive_streaming_text, ignore = DeliveryMechanism.lookup(
            self._db, DeliveryMechanism.STREAMING_TEXT_CONTENT_TYPE, DeliveryMechanism.OVERDRIVE_DRM)

    def test_implicit_medium(self):
        eq_(Edition.BOOK_MEDIUM, self.epub_no_drm.implicit_medium)
        eq_(Edition.BOOK_MEDIUM, self.epub_adobe_drm.implicit_medium)
        eq_(Edition.BOOK_MEDIUM, self.overdrive_streaming_text.implicit_medium)

    def test_is_media_type(self):
        eq_(False, DeliveryMechanism.is_media_type(None))
        eq_(True, DeliveryMechanism.is_media_type(Representation.EPUB_MEDIA_TYPE))
        eq_(False, DeliveryMechanism.is_media_type(DeliveryMechanism.KINDLE_CONTENT_TYPE))
        eq_(False, DeliveryMechanism.is_media_type(DeliveryMechanism.STREAMING_TEXT_CONTENT_TYPE))

    def test_is_streaming(self):
        eq_(False, self.epub_no_drm.is_streaming)
        eq_(False, self.epub_adobe_drm.is_streaming)
        eq_(True, self.overdrive_streaming_text.is_streaming)

    def test_drm_scheme_media_type(self):
        eq_(None, self.epub_no_drm.drm_scheme_media_type)
        eq_(DeliveryMechanism.ADOBE_DRM, self.epub_adobe_drm.drm_scheme_media_type)
        eq_(None, self.overdrive_streaming_text.drm_scheme_media_type)

    def test_content_type_media_type(self):
        eq_(Representation.EPUB_MEDIA_TYPE, self.epub_no_drm.content_type_media_type)
        eq_(Representation.EPUB_MEDIA_TYPE, self.epub_adobe_drm.content_type_media_type)
        eq_(Representation.TEXT_HTML_MEDIA_TYPE + DeliveryMechanism.STREAMING_PROFILE,
            self.overdrive_streaming_text.content_type_media_type)

class TestCustomListEntry(DatabaseTest):

    def test_set_license_pool(self):

        # Start with a custom list with no entries
        list, ignore = self._customlist(num_entries=0)

        # Now create an entry with an edition but no license pool.
        edition = self._edition()

        entry, ignore = get_one_or_create(
            self._db, CustomListEntry,
            list_id=list.id, edition_id=edition.id,
        )

        eq_(edition, entry.edition)
        eq_(None, entry.license_pool)

        # Here's another edition, with a license pool.
        other_edition, lp = self._edition(with_open_access_download=True)

        # And its identifier is equivalent to the entry's edition's identifier.
        data_source = DataSource.lookup(self._db, DataSource.OCLC)
        lp.identifier.equivalent_to(data_source, edition.primary_identifier, 1)

        # If we call set_license_pool, it should find the license pool
        # from the equivalent identifier.
        entry.set_license_pool()

        eq_(lp, entry.license_pool)


class TestCollection(DatabaseTest):

    def setup(self):
        super(TestCollection, self).setup()
        self.collection = self._collection()

    def test_encrypts_client_secret(self):
        collection, new = get_one_or_create(
            self._db, Collection, name=u"Test Collection", client_id=u"test",
            client_secret=u"megatest"
        )
        assert collection.client_secret != u"megatest"
        eq_(True, collection.client_secret.startswith("$2a$"))

    def test_register(self):
        collection, plaintext_secret = Collection.register(
            self._db, u"A Library"
        )

        # It creates client details and a DataSource for the collection
        assert collection.client_id and collection.client_secret
        assert get_one(self._db, DataSource, name=collection.name)

        # It returns nothing if the name is already taken.
        assert_raises(ValueError, Collection.register, self._db, u"A Library")

    def test_authenticate(self):

        result = Collection.authenticate(self._db, u"abc", u"def")
        eq_(self.collection, result)

        result = Collection.authenticate(self._db, u"abc", u"bad_secret")
        eq_(None, result)

        result = Collection.authenticate(self._db, u"bad_id", u"def")
        eq_(None, result)

    def test_catalog_identifier(self):
        """#catalog_identifier associates an identifier with the collection"""

        identifier = self._identifier()
        self.collection.catalog_identifier(self._db, identifier)
        eq_(1, len(self.collection.catalog))
        eq_(identifier, self.collection.catalog[0])

    def test_works_updated_since(self):

        w1 = self._work(with_license_pool=True)
        w2 = self._work(with_license_pool=True)
        w3 = self._work(with_license_pool=True)
        timestamp = datetime.datetime.utcnow()
        # A collection with no catalog returns nothing.
        eq_([], self.collection.works_updated_since(self._db, timestamp).all())

        # When no timestamp is passed, all works in the catalog are returned.
        self.collection.catalog_identifier(self._db, w1.license_pools[0].identifier)
        self.collection.catalog_identifier(self._db, w2.license_pools[0].identifier)
        updated_works = self.collection.works_updated_since(self._db, None).all()

        eq_(2, len(updated_works))
        assert w1 in updated_works and w2 in updated_works
        assert w3 not in updated_works

        # When a timestamp is passed, only works that have been updated
        # since then will be returned
        w1.coverage_records[0].timestamp = datetime.datetime.utcnow()
        eq_([w1], self.collection.works_updated_since(self._db, timestamp).all())


class TestMaterializedViews(DatabaseTest):

    def test_license_pool_is_works_preferred_license_pool(self):
        """Verify that the license_pool_id stored in the materialized views
        identifies the LicensePool associated with the Work's
        presentation edition, not some other LicensePool.
        """
        # Create a Work with two LicensePools
        work = self._work(with_license_pool=True)
        [pool1] = work.license_pools
        edition2, pool2 = self._edition(with_license_pool=True)
        work.license_pools.append(pool1)
        eq_(pool1, work.presentation_edition.license_pool)
        work.presentation_ready = True
        work.simple_opds_entry = '<entry>'
        work.assign_genres_from_weights({classifier.Fantasy : 1})

        # Make sure the Work shows up in the materialized view.
        SessionManager.refresh_materialized_views(self._db)

        from model import (
            MaterializedWork as mwc,
            MaterializedWorkWithGenre as mwgc,
        )
        [mw] = self._db.query(mwc).all()
        [mwg] = self._db.query(mwgc).all()

        eq_(pool1.id, mw.license_pool_id)
        eq_(pool1.id, mwg.license_pool_id)

        # If we change the Work's preferred edition, we change the
        # license_pool_id that gets stored in the materialized views.
        work.set_presentation_edition(edition2)
        SessionManager.refresh_materialized_views(self._db)
        [mw] = self._db.query(mwc).all()
        [mwg] = self._db.query(mwgc).all()

        eq_(pool2.id, mw.license_pool_id)
        eq_(pool2.id, mwg.license_pool_id)

    def test_license_data_source_is_stored_in_views(self):
        """Verify that the data_source_name stored in the materialized views
        is the DataSource associated with the LicensePool, not the
        DataSource associated with the presentation Edition.
        """

        # Create a Work whose LicensePool has three Editions: one from
        # Gutenberg (created by default), one from the admin interface
        # (created manually), and one generated by the presentation
        # edition generator, which synthesizes the other two.
        work = self._work(with_license_pool=True)

        [pool] = work.license_pools
        gutenberg_edition = pool.presentation_edition

        identifier = pool.identifier
        staff_edition = self._edition(
            data_source_name=DataSource.LIBRARY_STAFF, 
            identifier_type=identifier.type, 
            identifier_id=identifier.identifier
        )
        staff_edition.title = u"staff chose this title"
        staff_edition.sort_title = u"staff chose this title"
        pool.set_presentation_edition()
        work.set_presentation_edition(pool.presentation_edition)

        # The presentation edition has the title taken from the admin
        # interface, but it was created by the presentation edition
        # generator.
        presentation_edition = pool.presentation_edition
        eq_("staff chose this title", presentation_edition.title)
        eq_(DataSource.PRESENTATION_EDITION, 
            presentation_edition.data_source.name
        )

        # Make sure the Work will show up in the materialized view.
        work.presentation_ready = True
        work.simple_opds_entry = '<entry>'
        work.assign_genres_from_weights({classifier.Fantasy : 1})

        SessionManager.refresh_materialized_views(self._db)

        from model import (
            MaterializedWork as mwc,
            MaterializedWorkWithGenre as mwgc,
        )
        [mw] = self._db.query(mwc).all()
        [mwg] = self._db.query(mwgc).all()

        # We would expect the data source to be Gutenberg, since
        # that's the edition associated with the LicensePool, and not
        # the data source of the Work's presentation edition.
        eq_(pool.data_source.name, mw.name)
        eq_(pool.data_source.name, mwg.name)

        # However, we would expect the title of the work to come from
        # the presentation edition.
        eq_("staff chose this title", mw.sort_title)

        # And since the data_source_id is the ID of the data source
        # associated with the presentation edition, we would expect it
        # to be the data source ID of the presentation edition.
        eq_(presentation_edition.data_source.id, mw.data_source_id)
        eq_(presentation_edition.data_source.id, mwg.data_source_id)
