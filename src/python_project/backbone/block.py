from __future__ import annotations

import logging
import time
from binascii import hexlify
from collections import namedtuple
from hashlib import sha256
from typing import List, Any

from ipv8.keyvault.crypto import default_eccrypto
from ipv8.messaging.serialization import default_serializer, PackError
from python_project.backbone.datastore.database import BaseDB
from python_project.backbone.datastore.utils import (
    decode_links,
    encode_links,
    shorten,
    Links,
    BytesLinks,
    GENESIS_DOT,
    Dot,
    EMPTY_PK,
    GENESIS_SEQ,
    GENESIS_LINK,
    UNKNOWN_SEQ,
    EMPTY_SIG,
)
from python_project.backbone.payload import BlockPayload

SKIP_ATTRIBUTES = {
    "key",
    "serializer",
    "crypto",
    "_logger",
    "_previous",
    "_links",
}


class PlexusBlock(object):
    """
    Container for Plexus block information
    """

    Data = namedtuple(
        "Data",
        [
            "type",
            "transaction",
            "public_key",
            "sequence_number",
            "previous",
            "links",
            "com_id",
            "com_seq_num",
            "timestamp",
            "insert_time",
            "signature",
        ],
    )

    def __init__(self, data=None, serializer=default_serializer):
        """
        Create a new PlexusBlock or load from an existing database entry.

        :param data: Optional data to initialize this block with.
        :type data: Block.Data or list
        :param serializer: An optional custom serializer to use for this block.
        :type serializer: Serializer
        """
        super(PlexusBlock, self).__init__()
        self.serializer = serializer
        if data is None:
            # data
            self.type = b"unknown"
            self.transaction = b""
            # block identity
            self.public_key = EMPTY_PK
            self.sequence_number = GENESIS_SEQ

            # previous hash in the personal chain
            self.previous = GENESIS_LINK
            self._previous = encode_links(self.previous)

            # Linked blocks => links to the block in other chains
            self.links = GENESIS_LINK
            self._links = encode_links(self.links)

            # Metadata for community identifiers
            self.com_id = EMPTY_PK
            self.com_seq_num: int = UNKNOWN_SEQ

            # Creation timestamp
            self.timestamp = int(time.time() * 1000)
            # Signature for the block
            self.signature = EMPTY_SIG
            # debug stuff
            self.insert_time = None
        else:
            self.transaction = data[1] if isinstance(data[1], bytes) else bytes(data[1])
            self._previous = (
                BytesLinks(data[4]) if isinstance(data[4], bytes) else bytes(data[4])
            )
            self._links = (
                BytesLinks(data[5]) if isinstance(data[5], bytes) else bytes(data[5])
            )

            self.previous = decode_links(self._previous)
            self.links = decode_links(self._links)

            self.type, self.public_key, self.sequence_number = data[0], data[2], data[3]
            self.com_id, self.com_seq_num = data[6], int(data[7])
            self.signature, self.timestamp, self.insert_time = (
                data[8],
                data[9],
                data[10],
            )

            self.type = (
                self.type
                if isinstance(self.type, bytes)
                else str(self.type).encode("utf-8")
            )
            self.public_key = (
                self.public_key
                if isinstance(self.public_key, bytes)
                else bytes(self.public_key)
            )
            self.signature = (
                self.signature
                if isinstance(self.signature, bytes)
                else bytes(self.signature)
            )

        self.hash = self.calculate_hash()
        self.crypto = default_eccrypto
        self._logger = logging.getLogger(self.__class__.__name__)

    def __str__(self):
        # This makes debugging and logging easier
        return "Block {0} from ...{1}:{2} links {3} for {4} type {5} cseq {6} cid {7}".format(
            self.short_hash,
            shorten(self.public_key),
            self.sequence_number,
            self.links,
            self.transaction,
            self.type,
            self.com_seq_num,
            self.com_id,
        )

    @property
    def short_hash(self):
        return shorten(self.hash)

    def __hash__(self):
        return self.hash_number

    @property
    def pers_dot(self) -> Dot:
        return Dot((self.sequence_number, self.short_hash))

    @property
    def com_dot(self) -> Dot:
        return Dot((self.com_seq_num, self.short_hash))

    @property
    def hash_number(self):
        """
        Return the hash of this block as a number (used as crawl ID).
        """
        return int(hexlify(self.hash), 16) % 100000000

    def calculate_hash(self) -> bytes:
        return sha256(self.pack()).digest()

    def __eq__(self, other: PlexusBlock) -> bool:
        if not isinstance(other, PlexusBlock):
            return False
        return self.pack() == other.pack()

    @property
    def is_peer_genesis(self) -> bool:
        return self.sequence_number == GENESIS_SEQ and self.previous == GENESIS_LINK

    def block_args(self, signature: bool = True) -> List[Any]:
        args = [
            self.type,
            self.transaction,
            self.public_key,
            self.sequence_number,
            self._previous,
            self._links,
            self.com_id,
            self.com_seq_num,
            self.signature if signature else EMPTY_SIG,
            self.timestamp,
        ]
        return args

    def to_block_payload(self, signature: bool = True) -> BlockPayload:
        return BlockPayload(*self.block_args(signature))

    def pack(self, signature: bool = True) -> bytes:
        """
        Encode the block
        Args:
            signature: False to pack EMPTY_SIG in the signature location, true to pack the signature field
        Returns:
            Block bytes
        """
        return self.serializer.pack_multiple(
            self.to_block_payload(signature).to_pack_list()
        )[0]

    @classmethod
    def unpack(
        cls, block_blob: bytes, serializer: Any = default_serializer
    ) -> PlexusBlock:
        payload = serializer.ez_unpack_serializables([BlockPayload], block_blob)
        return PlexusBlock.from_payload(payload[0], serializer)

    @classmethod
    def from_payload(
        cls, payload: BlockPayload, serializer=default_serializer
    ) -> PlexusBlock:
        """
        Create a block according to a given payload and serializer.
        This method can be used when receiving a block from the network.
        """
        return cls(
            [
                payload.type,
                payload.transaction,
                payload.public_key,
                payload.sequence_number,
                payload.previous,
                payload.links,
                payload.com_id,
                payload.com_seq_num,
                payload.signature,
                payload.timestamp,
                time.time(),
            ],
            serializer,
        )

    def sign(self, key):
        """
        Signs this block with the given key
        :param key: the key to sign this block with
        """
        self.signature = self.crypto.create_signature(key, self.pack(signature=False))
        self.hash = self.calculate_hash()

    @classmethod
    def create(
        cls,
        block_type: bytes,
        transaction: bytes,
        database: BaseDB,
        public_key: bytes,
        com_id: bytes = None,
        com_links: Links = None,
        pers_links: Links = None,
    ):
        """
        Create PlexusBlock wrt local database knowledge.

        Args:
            block_type: type of the block in bytes
            transaction: transaction blob bytes
            database: local database with chains
            public_key: public key of the block creator
            link_filter: Filter object to attack to block that are valid according to the business logic
            com_id: id of the community which block is part of [optional]
            com_links: Explicitly link with these blocks [optional]
            pers_links: Create a block at a certain [optional]

        Returns:
            PlexusBlock

        """

        # Decide to link blocks in the personal chain:
        personal_chain = database.get_chain(public_key)
        if not personal_chain:
            # There are no blocks in the personal chain yet
            last_link = Links((GENESIS_DOT,))
        else:
            last_link = personal_chain.consistent_terminal

        # Fork personal chain at the
        if pers_links:
            # There is an explicit link for the previous link
            last_link = pers_links

        per_seq_num = max(last_link)[0] + 1

        # TODO: Add link filtering and choose links
        ret = cls()
        ret.type = block_type
        ret.transaction = transaction
        ret.sequence_number = per_seq_num
        ret.previous = last_link

        # --- Community related logic ---
        if com_id:
            ret.com_id = com_id
            # There is community specified => will base block on the latest known information + filters
            if com_links:
                last_com_links = com_links
                com_seq_num = max(last_com_links)[0]
            else:
                com_chain = database.get_chain(com_id)
                last_com_links = (
                    com_chain.consistent_terminal
                    if com_chain
                    else Links((GENESIS_DOT,))
                )
                # TODO: add link filtering here
                com_seq_num = max(last_com_links)[0] + 1

            ret.links = last_com_links
            ret.com_seq_num = com_seq_num
            ret.com_id = com_id

        ret._links = encode_links(ret.links)
        ret._previous = encode_links(ret.previous)

        ret.public_key = public_key
        ret.signature = EMPTY_SIG
        ret.hash = ret.calculate_hash()
        return ret

    def block_invariants_valid(self) -> bool:
        """Verify that block is valid wrt block invariants"""
        # 1. Sequence number should not be prior to genesis
        if self.sequence_number < GENESIS_SEQ and self.com_seq_num < GENESIS_SEQ:
            return False
        # 2. Timestamp should be non negative
        if self.timestamp < 0:
            return False
        # 3. Public key and signature should be valid
        if not self.crypto.is_valid_public_bin(self.public_key):
            return False
        else:
            try:
                pck = self.pack(signature=False)
            except PackError as _:
                pck = None
            if pck is None or not self.crypto.is_valid_signature(
                self.crypto.key_from_public_bin(self.public_key), pck, self.signature
            ):
                return False
        return True
