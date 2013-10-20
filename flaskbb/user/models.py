# -*- coding: utf-8 -*-
"""
    flaskbb.user.models
    ~~~~~~~~~~~~~~~~~~~~

    This module provides the models for the user.

    :copyright: (c) 2013 by the FlaskBB Team.
    :license: BSD, see LICENSE for more details.
"""
import sys
from datetime import datetime

from itsdangerous import TimedJSONWebSignatureSerializer as Serializer
from itsdangerous import SignatureExpired
from werkzeug import generate_password_hash, check_password_hash
from flask import current_app
from flask.ext.login import UserMixin, AnonymousUserMixin
from flaskbb.extensions import db, cache
from flaskbb.forum.models import Post, Topic, topictracker


groups_users = db.Table('groups_users',
    db.Column('user_id', db.Integer(), db.ForeignKey('users.id')),
    db.Column('group_id', db.Integer(), db.ForeignKey('groups.id')))


class Group(db.Model):
    __tablename__ = "groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True)
    description = db.Column(db.String(80))

    # I bet there is a nicer way for this :P
    admin = db.Column(db.Boolean, default=False)
    super_mod = db.Column(db.Boolean, default=False)
    mod = db.Column(db.Boolean, default=False)
    guest = db.Column(db.Boolean, default=False)
    banned = db.Column(db.Boolean, default=False)

    editpost = db.Column(db.Boolean, default=True)
    deletepost = db.Column(db.Boolean, default=False)
    deletetopic = db.Column(db.Boolean, default=False)
    posttopic = db.Column(db.Boolean, default=True)
    postreply = db.Column(db.Boolean, default=True)

    # Methods
    def __repr__(self):
        """
        Set to a unique key specific to the object in the database.
        Required for cache.memoize() to work across requests.
        """
        return "<{} {})>".format(self.__class__.__name__, self.id)

    def save(self):
        db.session.add(self)
        db.session.commit()
        return self

    def delete(self):
        db.session.delete(self)
        db.session.commit()
        return self


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String, unique=True)
    email = db.Column(db.String, unique=True)
    _password = db.Column('password', db.String(80), nullable=False)
    date_joined = db.Column(db.DateTime, default=datetime.utcnow())
    lastseen = db.Column(db.DateTime, default=datetime.utcnow())
    birthday = db.Column(db.DateTime)
    gender = db.Column(db.String)
    website = db.Column(db.String)
    location = db.Column(db.String)
    signature = db.Column(db.String)
    avatar = db.Column(db.String)
    notes = db.Column(db.Text(5000))

    posts = db.relationship("Post", backref="user", lazy="dynamic")
    topics = db.relationship("Topic", backref="user", lazy="dynamic")

    primary_group_id = db.Column(db.Integer, db.ForeignKey('groups.id'))

    primary_group = db.relationship('Group', lazy="joined",
                                    backref="user_group", uselist=False,
                                    foreign_keys=[primary_group_id])

    secondary_groups = \
        db.relationship('Group',
                        secondary=groups_users,
                        primaryjoin=(groups_users.c.user_id == id),
                        backref=db.backref('users', lazy='dynamic'),
                        lazy='dynamic')

    tracked_topics = \
        db.relationship("Topic", secondary=topictracker,
                        primaryjoin=(topictracker.c.user_id == id),
                        backref=db.backref("topicstracked", lazy="dynamic"),
                        lazy="dynamic")

    # Properties
    @property
    def post_count(self):
        """
        Property interface for get_post_count method.

        Method seperate for easy invalidation of cache.
        """
        return self.get_post_count()

    @property
    def last_post(self):
        """
        Property interface for get_last_post method.

        Method seperate for easy invalidation of cache.
        """
        return self.get_last_post()

    # Methods
    def __repr__(self):
        """
        Set to a unique key specific to the object in the database.
        Required for cache.memoize() to work across requests.
        """
        return "Username: %s" % self.username

    def _get_password(self):
        return self._password

    def _set_password(self, password):
        self._password = generate_password_hash(password)

    # Hide password encryption by exposing password field only.
    password = db.synonym('_password',
                          descriptor=property(_get_password,
                                              _set_password))

    def check_password(self, password):
        """
        Check passwords. If passwords match it returns true, else false
        """
        if self.password is None:
            return False
        return check_password_hash(self.password, password)

    @classmethod
    def authenticate(cls, login, password):
        """
        A classmethod for authenticating users
        It returns true if the user exists and has entered a correct password
        """
        user = cls.query.filter(db.or_(User.username == login,
                                       User.email == login)).first()

        if user:
            authenticated = user.check_password(password)
        else:
            authenticated = False
        return user, authenticated

    def _make_token(self, data, timeout):
        s = Serializer(current_app.config['SECRET_KEY'], timeout)
        return s.dumps(data)

    def _verify_token(self, token):
        s = Serializer(current_app.config['SECRET_KEY'])
        data = None
        expired, invalid = False, False
        try:
            data = s.loads(token)
        except SignatureExpired:
            expired = True
        except Exception:
            invalid = True
        return expired, invalid, data

    def make_reset_token(self, expiration=3600):
        return self._make_token({'id': self.id, 'op': 'reset'}, expiration)

    def verify_reset_token(self, token):
        expired, invalid, data = self._verify_token(token)
        if data and data.get('id') == self.id and data.get('op') == 'reset':
            data = True
        else:
            data = False
        return expired, invalid, data

    def all_topics(self, page):
        """
        Returns a paginated query result with all topics the user has created.
        """
        return Topic.query.filter(Topic.user_id == self.id).\
            filter(Post.topic_id == Topic.id).\
            order_by(Post.id.desc()).\
            paginate(page, current_app.config['TOPICS_PER_PAGE'], False)

    def all_posts(self, page):
        """
        Returns a paginated query result with all posts the user has created.
        """
        return Post.query.filter(Post.user_id == self.id).\
            paginate(page, current_app.config['TOPICS_PER_PAGE'], False)

    def track_topic(self, topic):
        """
        Tracks the specified topic
        """
        if not self.is_tracking_topic(topic):
            self.tracked_topics.append(topic)
            return self

    def untrack_topic(self, topic):
        """
        Untracks the specified topic
        """
        if self.is_tracking_topic(topic):
            self.tracked_topics.remove(topic)
            return self

    def is_tracking_topic(self, topic):
        """
        Checks if the user is already tracking this topic
        """
        return self.tracked_topics.filter(
            topictracker.c.topic_id == topic.id).count() > 0

    def add_to_group(self, group):
        """
        Adds the user to the `group` if he isn't in it.
        """
        if not self.in_group(group):
            self.secondary_groups.append(group)
            return self

    def remove_from_group(self, group):
        """
        Removes the user from the `group` if he is in it.
        """
        if self.in_group(group):
            self.secondary_groups.remove(group)
            return self

    def in_group(self, group):
        """
        Returns True if the user is in the specified group
        """
        return self.secondary_groups.filter(
            groups_users.c.group_id == group.id).count() > 0

    @cache.memoize(60*5)
    def get_permissions(self, exclude=None):
        """
        Returns a dictionary with all the permissions the user has.
        """
        exclude = exclude or []
        exclude.extend(['id', 'name', 'description'])

        perms = {}
        groups = self.secondary_groups.all()
        groups.append(self.primary_group)
        for group in groups:
            for c in group.__table__.columns:
                # try if the permission already exists in the dictionary
                # and if the permission is true, set it to True
                try:
                    if not perms[c.name] and getattr(group, c.name):
                        perms[c.name] = True

                # if the permission doesn't exist in the dictionary
                # add it to the dictionary
                except KeyError:
                    # if the permission is in the exclude list,
                    # skip to the next permission
                    if c.name in exclude:
                        continue
                    perms[c.name] = getattr(group, c.name)
        return perms

    def save(self, groups=None):
        if groups:
            # TODO: Only remove/add groups that are selected
            secondary_groups = self.secondary_groups.all()
            for group in secondary_groups:
                self.remove_from_group(group)
            db.session.commit()

            for group in groups:
                # Do not add the primary group to the secondary groups
                if group.id == self.primary_group_id:
                    continue
                self.add_to_group(group)
        db.session.add(self)
        db.session.commit()
        return self

    @cache.memoize(timeout=sys.maxint)
    def get_post_count(self):
        """
        Returns the amount of posts within the current topic.
        """
        return Post.query.filter(Post.user_id == self.id).\
            count()

    # @cache.memoize(timeout=sys.maxint)  # TODO:  DetachedInstanceError if we return a Flask-SQLAlchemy model.
    def get_last_post(self):
        """
        Returns the latest post from the user
        """
        return Post.query.filter(Post.user_id == self.id).\
            order_by(Post.date_created.desc()).first()

    def invalidate_cache(self):
        """
        Invalidates this objects cached metadata.
        """
        cache.delete_memoized(self.get_post_count, self)
        #cache.delete_memoized(self.get_last_post, self) # TODO:  Cannot use til we can cache this object.


class Guest(AnonymousUserMixin):
    @cache.memoize(60*5)
    def get_permissions(self, exclude=None):
        """
        Returns a dictionary with all permissions the user has
        """
        exclude = exclude or []
        exclude.extend(['id', 'name', 'description'])

        perms = {}
        # Get the Guest group
        group = Group.query.filter_by(guest=True).first()
        for c in group.__table__.columns:
            if c.name in exclude:
                continue
            perms[c.name] = getattr(group, c.name)
        return perms
