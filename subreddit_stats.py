#!/usr/bin/env python
import re
import six
import sys
import time
from collections import defaultdict
from datetime import datetime
from optparse import OptionGroup, OptionParser

from praw import Reddit
from praw.errors import ClientException, ExceptionList, RateLimitExceeded
from praw.objects import Comment, Redditor

DAYS_IN_SECONDS = 60 * 60 * 24
MAX_BODY_SIZE = 10000

__version__ = '0.3.0'


class SubRedditStats(object):
    post_prefix = 'Subreddit Stats:'
    post_header = '---\n###%s\n'
    post_footer = ('>Generated with [BBoe](/user/bboe)\'s [Subreddit Stats]'
                   '(https://github.com/bboe/subreddit_stats)  \n%s'
                   'SRS Marker: %d')
    re_marker = re.compile('SRS Marker: (\d+)')

    @staticmethod
    def _previous_max(submission):
        try:
            val = SubRedditStats.re_marker.findall(submission.selftext)[-1]
            return float(val)
        except (IndexError, TypeError):
            print('End marker not found in previous submission. Aborting')
            sys.exit(1)

    @staticmethod
    def _permalink(permalink):
        tokens = permalink.split('/')
        if tokens[8] == '':  # submission
            return '/comments/%s/_/' % (tokens[6])
        else:  # comment
            return '/comments/%s/_/%s?context=1' % (tokens[6], tokens[8])

    @staticmethod
    def _user(user):
        if user is None:
            return '_deleted_'
        elif isinstance(user, Redditor):
            user = str(user)
        return '[%s](/user/%s)' % (user.replace('_', '\_'), user)

    @staticmethod
    def _submit(func, *args, **kwargs):
        def sleep(sleep_time):
            print('\tSleeping for %d seconds' % sleep_time)
            time.sleep(sleep_time)

        while True:
            try:
                return func(*args, **kwargs)
            except RateLimitExceeded as error:
                sleep(error.sleep_time)
            except ExceptionList as exception_list:
                for error in exception_list.errors:
                    if isinstance(error, RateLimitExceeded):
                        sleep(error.sleep_time)
                        break
                else:
                    raise

    def __init__(self, subreddit, site, verbosity):
        self.reddit = Reddit(str(self), site)
        self.subreddit = self.reddit.get_subreddit(subreddit)
        self.verbosity = verbosity
        self.submissions = []
        self.comments = []
        self.submitters = defaultdict(list)
        self.commenters = defaultdict(list)
        self.min_date = 0
        self.max_date = time.time() - DAYS_IN_SECONDS * 3
        self.prev_srs = None
        # Config
        self.reddit.config.comment_limit = -1  # Fetch max comments possible
        self.reddit.config.comment_sort = 'top'

    def __str__(self):
        return 'BBoe\'s Subreddit Stats %s' % __version__

    def login(self, user, pswd):
        if self.verbosity > 0:
            print('Logging in')
        self.reddit.login(user, pswd)

    def msg(self, msg, level, overwrite=False):
        if self.verbosity >= level:
            sys.stdout.write(msg)
            if overwrite:
                sys.stdout.write('\r')
                sys.stdout.flush()
            else:
                sys.stdout.write('\n')

    def prev_stat(self, prev_url):
        submission = self.reddit.get_submission(prev_url)
        self.min_date = self._previous_max(submission)
        self.prev_srs = prev_url

    def fetch_recent_submissions(self, max_duration, after, exclude_self,
                                 since_last=True):
        '''Fetches recent submissions in subreddit with boundaries.

        Does not include posts within the last three days as their scores may
        not be representative.

        Keyword arguments:
        max_duration -- When set, specifies the number of days to include
        after -- When set, fetch all submission after this submission id.
        exclude_self -- When true, don't include self posts.
        since_last -- When true use info from last submission to determine the
                      stop point
        '''
        if max_duration:
            self.min_date = self.max_date - DAYS_IN_SECONDS * max_duration
        url_data = {'after': after} if after else None
        self.msg('DEBUG: Fetching submissions', 1)
        for submission in self.subreddit.get_new_by_date(limit=None,
                                                         url_data=url_data):
            if submission.created_utc > self.max_date:
                continue
            if submission.created_utc <= self.min_date:
                break
            if (since_last and str(submission.author) == str(self.reddit.user)
                and submission.title.startswith(self.post_prefix)):
                # Use info in this post to update the min_date
                # And don't include this post
                self.msg('Found previous: %s' % submission.title, 2)
                if self.prev_srs is None:  # Only use the most recent
                    self.min_date = max(self.min_date,
                                        self._previous_max(submission))
                    self.prev_srs = submission.permalink
                continue
            if exclude_self and submission.is_self:
                continue
            self.submissions.append(submission)
        self.msg('DEBUG: Found %d submissions' % len(self.submissions), 1)
        if len(self.submissions) == 0:
            return False

        # Update real min and max dates
        self.submissions.sort(key=lambda x: x.created_utc)
        self.min_date = self.submissions[0].created_utc
        self.max_date = self.submissions[-1].created_utc
        return True

    def fetch_top_submissions(self, top, exclude_self):
        '''Fetches top 1000 submissions by some top value.

        Keyword arguments:
        top -- One of week, month, year, all
        exclude_self -- When true, don't include self posts.
        '''
        if top not in ('day', 'week', 'month', 'year', 'all'):
            raise TypeError('%r is not a valid top value' % top)
        self.msg('DEBUG: Fetching submissions', 1)
        url_data = {'t': top}
        for submission in self.subreddit.get_top(limit=None,
                                                 url_data=url_data):
            if exclude_self and submission.is_self:
                continue
            self.submissions.append(submission)
        self.msg('DEBUG: Found %d submissions' % len(self.submissions), 1)
        if len(self.submissions) == 0:
            return False

        # Update real min and max dates
        self.submissions.sort(key=lambda x: x.created_utc)
        self.min_date = self.submissions[0].created_utc
        self.max_date = self.submissions[-1].created_utc
        return True

    def process_submitters(self):
        self.msg('DEBUG: Processing Submitters', 1)
        for submission in self.submissions:
            if submission.author:
                self.submitters[str(submission.author)].append(submission)

    def process_commenters(self):
        num = len(self.submissions)
        self.msg('DEBUG: Processing Commenters on %d submissions' % num, 1)
        for i, submission in enumerate(self.submissions):
            self.msg('%d/%d submissions' % (i + 1, num), 2, overwrite=True)
            if submission.num_comments == 0:
                continue
            try:
                self.comments.extend(submission.all_comments_flat)
            except Exception as exception:
                print('Exception fetching comments on %r: %s' %
                      (submission.content_id, str(exception)))
            for orphans in six.itervalues(submission._orphaned):
                self.comments.extend(orphans)
        for comment in self.comments:
            if comment.author:
                self.commenters[str(comment.author)].append(comment)

    def basic_stats(self):
        sub_ups = sum(x.ups for x in self.submissions)
        sub_downs = sum(x.downs for x in self.submissions)
        comm_ups = sum(x.ups for x in self.comments)
        comm_downs = sum(x.downs for x in self.comments)

        sub_up_perc = sub_ups * 100 / (sub_ups + sub_downs)
        comm_up_perc = comm_ups * 100 / (comm_ups + comm_downs)

        values = [('Total', len(self.submissions), '', len(self.comments), ''),
                  ('Unique Redditors', len(self.submitters), '',
                   len(self.commenters), ''),
                  ('Upvotes', sub_ups, '%d%%' % sub_up_perc,
                   comm_ups, '%d%%' % comm_up_perc),
                  ('Downvotes', sub_downs, '%d%%' % (100 - sub_up_perc),
                   comm_downs, '%d%%' % (100 - comm_up_perc))]

        retval = '||Submissions|%|Comments|%|\n:-:|--:|--:|--:|--:\n'
        for quad in values:
            retval += '__%s__|%d|%s|%d|%s\n' % quad
        return '%s\n' % retval

    def top_submitters(self, num, num_submissions):
        num = min(num, len(self.submitters))
        if num <= 0:
            return ''

        top_submitters = sorted(six.iteritems(self.submitters), reverse=True,
                                key=lambda x: (sum(y.score for y in x[1]),
                                               len(x[1])))[:num]

        retval = self.post_header % 'Top Submitters\' Top Submissions'
        for (author, submissions) in top_submitters:
            retval += '0. %d pts, %d submissions: %s\n' % (
                sum(x.score for x in submissions), len(submissions),
                self._user(author))
            for sub in sorted(submissions, reverse=True,
                              key=lambda x: x.score)[:num_submissions]:
                title = sub.title.replace('\n', ' ').strip()
                if sub.permalink != sub.url:
                    retval += '  0. [%s](%s)' % (title, sub.url)
                else:
                    retval += '  0. %s' % title
                retval += ' (%d pts, [%d comments](%s))\n' % (
                    sub.score, sub.num_comments,
                    self._permalink(sub.permalink))
            retval += '\n'
        return retval

    def top_commenters(self, num):
        score = lambda x: x.ups - x.downs

        num = min(num, len(self.commenters))
        if num <= 0:
            return ''

        top_commenters = sorted(six.iteritems(self.commenters), reverse=True,
                                key=lambda x: (sum(score(y) for y in x[1]),
                                               len(x[1])))[:num]

        retval = self.post_header % 'Top Commenters'
        for author, comments in top_commenters:
            retval += '0. %s (%d pts, %d comments)\n' % (
                self._user(author), sum(score(x) for x in comments),
                len(comments))
        return '%s\n' % retval

    def top_submissions(self, num):
        num = min(num, len(self.submissions))
        if num <= 0:
            return ''

        top_submissions = sorted(self.submissions, reverse=True,
                                 key=lambda x: x.score)[:num]

        retval = self.post_header % 'Top Submissions'
        for sub in top_submissions:
            title = sub.title.replace('\n', ' ').strip()
            if sub.permalink != sub.url:
                retval += '0. [%s](%s)' % (title, sub.url)
            else:
                retval += '0. %s' % title
            retval += ' by %s (%d pts, [%d comments](%s))\n' % (
                self._user(sub.author), sub.score, sub.num_comments,
                self._permalink(sub.permalink))
        return '%s\n' % retval

    def top_comments(self, num):
        score = lambda x: x.ups - x.downs

        num = min(num, len(self.comments))
        if num <= 0:
            return ''

        top_comments = sorted(self.comments, reverse=True,
                              key=score)[:num]
        retval = self.post_header % 'Top Comments'
        for comment in top_comments:
            title = comment.submission.title.replace('\n', ' ').strip()
            retval += ('0. %d pts: %s\'s [comment](%s) in %s\n'
                       % (score(comment), self._user(comment.author),
                          self._permalink(comment.permalink), title))
        return '%s\n' % retval

    def publish_results(self, subreddit, submitters, commenters, submissions,
                        comments, top, debug=False):
        def timef(timestamp):
            dtime = datetime.fromtimestamp(timestamp)
            return dtime.strftime('%Y-%m-%d %H:%M PDT')

        title = '%s %s %ssubmissions from %s to %s' % (
            self.post_prefix, str(self.subreddit), 'top ' if top else '',
            timef(self.min_date), timef(self.max_date))
        if self.prev_srs:
            prev = '[Previous Stat](%s)  \n' % self._permalink(self.prev_srs)
        else:
            prev = ''

        basic = self.basic_stats()
        t_commenters = self.top_commenters(commenters)
        t_submissions = self.top_submissions(submissions)
        t_comments = self.top_comments(comments)
        footer = self.post_footer % (prev, self.max_date)

        body = ''
        num_submissions = 10
        while body == '' or len(body) > MAX_BODY_SIZE and num_submissions > 2:
            t_submitters = self.top_submitters(submitters, num_submissions)
            body = (basic + t_submitters + t_commenters + t_submissions +
                    t_comments + footer)
            num_submissions -= 1

        if len(body) > MAX_BODY_SIZE:
            print('The resulting message is too big. Not submitting.')
            debug = True

        if not debug:
            msg = ('You are about to submit to subreddit %s as %s.\n'
                   'Are you sure? yes/[no]: ' % (subreddit,
                                                 str(self.reddit.user)))
            sys.stdout.write(msg)
            sys.stdout.flush()
            if sys.stdin.readline().strip().lower() not in ['y', 'yes']:
                print('Submission aborted')
            else:
                try:
                    res = self._submit(self.reddit.submit, subreddit, title,
                                       text=body)
                    print(res.permalink)
                    return
                except Exception as error:
                    print('The submission failed:' + str(error))

        # We made it here either to debug=True or an error.
        print(title)
        print(body)


def main():
    msg = {
        'site': 'The site to connect to defined in your praw.ini file.',
        'user': ('The user to login as. If not specified the user (if any) '
                 'from the site config will be used, otherwise you will be '
                 'prompted for a username.'),
        'pswd': ('The password to use for login. Can only be used in '
                 'combination with "--user". See help for "--user".')}

    parser = OptionParser(usage='usage: %prog [options] subreddit')
    parser.add_option('-s', '--submitters', type='int', default=5,
                      help='Number of top submitters to display '
                      '[default %default]')
    parser.add_option('-c', '--commenters', type='int', default=10,
                      help='Number of top commenters to display '
                      '[default %default]')
    parser.add_option('-a', '--after',
                      help='Submission ID to fetch after')
    parser.add_option('-d', '--days', type='int', default=32,
                      help=('Number of previous days to include submissions '
                            'from. Use 0 for unlimited. Default: %default'))
    parser.add_option('-v', '--verbose', action='count', default=0,
                      help='Increase the verbosity by 1')
    parser.add_option('-D', '--debug', action='store_true',
                      help='Enable debugging mode. Does not post stats.')
    parser.add_option('-R', '--submission-reddit',
                      help=('Subreddit to submit to. If not present, '
                            'submits to the subreddit processed'))
    parser.add_option('-t', '--top',
                      help=('Run on top submissions either by day, week, '
                            'month, year, or all'))
    parser.add_option('', '--no-self', action='store_true',
                      help=('Do not include self posts (and their comments) in'
                            ' the calculation. '))
    parser.add_option('', '--prev',
                      help='Statically provide the URL of previous SRS page.')

    group = OptionGroup(parser, 'Site/Authentication options')
    group.add_option('-S', '--site', help=msg['site'])
    group.add_option('-u', '--user', help=msg['user'])
    group.add_option('-p', '--pswd', help=msg['pswd'])
    parser.add_option_group(group)

    options, args = parser.parse_args()
    if len(args) != 1:
        parser.error('Must provide subreddit')

    if options.submission_reddit:
        submission_reddit = options.submission_reddit
    else:
        submission_reddit = args[0]

    srs = SubRedditStats(args[0], options.site, options.verbose)
    srs.login(options.user, options.pswd)
    if options.prev:
        srs.prev_stat(options.prev)
    if options.top:
        found = srs.fetch_top_submissions(options.top, options.no_self)
    else:
        found = srs.fetch_recent_submissions(max_duration=options.days,
                                             after=options.after,
                                             exclude_self=options.no_self)
    if not found:
        print('No submissions were found.')
        return 1
    srs.process_submitters()
    if options.commenters > 0:
        srs.process_commenters()
    srs.publish_results(submission_reddit, options.submitters,
                        options.commenters, 5, 5, options.top, options.debug)


if __name__ == '__main__':
    sys.exit(main())
