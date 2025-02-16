# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: signer.proto
# Protobuf Python Version: 5.26.1
"""Generated protocol buffer code."""
from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import symbol_database as _symbol_database
from google.protobuf.internal import builder as _builder
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()




DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\x0csigner.proto\x12\x07signrpc\"3\n\nKeyLocator\x12\x12\n\nkey_family\x18\x01 \x01(\x05\x12\x11\n\tkey_index\x18\x02 \x01(\x05\"L\n\rKeyDescriptor\x12\x15\n\rraw_key_bytes\x18\x01 \x01(\x0c\x12$\n\x07key_loc\x18\x02 \x01(\x0b\x32\x13.signrpc.KeyLocator\")\n\x05TxOut\x12\r\n\x05value\x18\x01 \x01(\x03\x12\x11\n\tpk_script\x18\x02 \x01(\x0c\"\x81\x02\n\x0eSignDescriptor\x12(\n\x08key_desc\x18\x01 \x01(\x0b\x32\x16.signrpc.KeyDescriptor\x12\x14\n\x0csingle_tweak\x18\x02 \x01(\x0c\x12\x14\n\x0c\x64ouble_tweak\x18\x03 \x01(\x0c\x12\x11\n\ttap_tweak\x18\n \x01(\x0c\x12\x16\n\x0ewitness_script\x18\x04 \x01(\x0c\x12\x1e\n\x06output\x18\x05 \x01(\x0b\x32\x0e.signrpc.TxOut\x12\x0f\n\x07sighash\x18\x07 \x01(\r\x12\x13\n\x0binput_index\x18\x08 \x01(\x05\x12(\n\x0bsign_method\x18\t \x01(\x0e\x32\x13.signrpc.SignMethod\"r\n\x07SignReq\x12\x14\n\x0craw_tx_bytes\x18\x01 \x01(\x0c\x12+\n\nsign_descs\x18\x02 \x03(\x0b\x32\x17.signrpc.SignDescriptor\x12$\n\x0cprev_outputs\x18\x03 \x03(\x0b\x32\x0e.signrpc.TxOut\"\x1c\n\x08SignResp\x12\x10\n\x08raw_sigs\x18\x01 \x03(\x0c\"2\n\x0bInputScript\x12\x0f\n\x07witness\x18\x01 \x03(\x0c\x12\x12\n\nsig_script\x18\x02 \x01(\x0c\">\n\x0fInputScriptResp\x12+\n\rinput_scripts\x18\x01 \x03(\x0b\x32\x14.signrpc.InputScript\"\xae\x01\n\x0eSignMessageReq\x12\x0b\n\x03msg\x18\x01 \x01(\x0c\x12$\n\x07key_loc\x18\x02 \x01(\x0b\x32\x13.signrpc.KeyLocator\x12\x13\n\x0b\x64ouble_hash\x18\x03 \x01(\x08\x12\x13\n\x0b\x63ompact_sig\x18\x04 \x01(\x08\x12\x13\n\x0bschnorr_sig\x18\x05 \x01(\x08\x12\x1d\n\x15schnorr_sig_tap_tweak\x18\x06 \x01(\x0c\x12\x0b\n\x03tag\x18\x07 \x01(\x0c\"$\n\x0fSignMessageResp\x12\x11\n\tsignature\x18\x01 \x01(\x0c\"g\n\x10VerifyMessageReq\x12\x0b\n\x03msg\x18\x01 \x01(\x0c\x12\x11\n\tsignature\x18\x02 \x01(\x0c\x12\x0e\n\x06pubkey\x18\x03 \x01(\x0c\x12\x16\n\x0eis_schnorr_sig\x18\x04 \x01(\x08\x12\x0b\n\x03tag\x18\x05 \x01(\x0c\"\"\n\x11VerifyMessageResp\x12\r\n\x05valid\x18\x01 \x01(\x08\"\x80\x01\n\x10SharedKeyRequest\x12\x18\n\x10\x65phemeral_pubkey\x18\x01 \x01(\x0c\x12(\n\x07key_loc\x18\x02 \x01(\x0b\x32\x13.signrpc.KeyLocatorB\x02\x18\x01\x12(\n\x08key_desc\x18\x03 \x01(\x0b\x32\x16.signrpc.KeyDescriptor\"\'\n\x11SharedKeyResponse\x12\x12\n\nshared_key\x18\x01 \x01(\x0c\"-\n\tTweakDesc\x12\r\n\x05tweak\x18\x01 \x01(\x0c\x12\x11\n\tis_x_only\x18\x02 \x01(\x08\"?\n\x10TaprootTweakDesc\x12\x13\n\x0bscript_root\x18\x01 \x01(\x0c\x12\x16\n\x0ekey_spend_only\x18\x02 \x01(\x08\"\xb5\x01\n\x18MuSig2CombineKeysRequest\x12\x1a\n\x12\x61ll_signer_pubkeys\x18\x01 \x03(\x0c\x12\"\n\x06tweaks\x18\x02 \x03(\x0b\x32\x12.signrpc.TweakDesc\x12\x30\n\rtaproot_tweak\x18\x03 \x01(\x0b\x32\x19.signrpc.TaprootTweakDesc\x12\'\n\x07version\x18\x04 \x01(\x0e\x32\x16.signrpc.MuSig2Version\"x\n\x19MuSig2CombineKeysResponse\x12\x14\n\x0c\x63ombined_key\x18\x01 \x01(\x0c\x12\x1c\n\x14taproot_internal_key\x18\x02 \x01(\x0c\x12\'\n\x07version\x18\x04 \x01(\x0e\x32\x16.signrpc.MuSig2Version\"\x9d\x02\n\x14MuSig2SessionRequest\x12$\n\x07key_loc\x18\x01 \x01(\x0b\x32\x13.signrpc.KeyLocator\x12\x1a\n\x12\x61ll_signer_pubkeys\x18\x02 \x03(\x0c\x12\"\n\x1aother_signer_public_nonces\x18\x03 \x03(\x0c\x12\"\n\x06tweaks\x18\x04 \x03(\x0b\x32\x12.signrpc.TweakDesc\x12\x30\n\rtaproot_tweak\x18\x05 \x01(\x0b\x32\x19.signrpc.TaprootTweakDesc\x12\'\n\x07version\x18\x06 \x01(\x0e\x32\x16.signrpc.MuSig2Version\x12 \n\x18pregenerated_local_nonce\x18\x07 \x01(\x0c\"\xbe\x01\n\x15MuSig2SessionResponse\x12\x12\n\nsession_id\x18\x01 \x01(\x0c\x12\x14\n\x0c\x63ombined_key\x18\x02 \x01(\x0c\x12\x1c\n\x14taproot_internal_key\x18\x03 \x01(\x0c\x12\x1b\n\x13local_public_nonces\x18\x04 \x01(\x0c\x12\x17\n\x0fhave_all_nonces\x18\x05 \x01(\x08\x12\'\n\x07version\x18\x06 \x01(\x0e\x32\x16.signrpc.MuSig2Version\"U\n\x1bMuSig2RegisterNoncesRequest\x12\x12\n\nsession_id\x18\x01 \x01(\x0c\x12\"\n\x1aother_signer_public_nonces\x18\x03 \x03(\x0c\"7\n\x1cMuSig2RegisterNoncesResponse\x12\x17\n\x0fhave_all_nonces\x18\x01 \x01(\x08\"P\n\x11MuSig2SignRequest\x12\x12\n\nsession_id\x18\x01 \x01(\x0c\x12\x16\n\x0emessage_digest\x18\x02 \x01(\x0c\x12\x0f\n\x07\x63leanup\x18\x03 \x01(\x08\"5\n\x12MuSig2SignResponse\x12\x1f\n\x17local_partial_signature\x18\x01 \x01(\x0c\"O\n\x17MuSig2CombineSigRequest\x12\x12\n\nsession_id\x18\x01 \x01(\x0c\x12 \n\x18other_partial_signatures\x18\x02 \x03(\x0c\"P\n\x18MuSig2CombineSigResponse\x12\x1b\n\x13have_all_signatures\x18\x01 \x01(\x08\x12\x17\n\x0f\x66inal_signature\x18\x02 \x01(\x0c\"*\n\x14MuSig2CleanupRequest\x12\x12\n\nsession_id\x18\x01 \x01(\x0c\"\x17\n\x15MuSig2CleanupResponse*\x9c\x01\n\nSignMethod\x12\x1a\n\x16SIGN_METHOD_WITNESS_V0\x10\x00\x12)\n%SIGN_METHOD_TAPROOT_KEY_SPEND_BIP0086\x10\x01\x12!\n\x1dSIGN_METHOD_TAPROOT_KEY_SPEND\x10\x02\x12$\n SIGN_METHOD_TAPROOT_SCRIPT_SPEND\x10\x03*b\n\rMuSig2Version\x12\x1c\n\x18MUSIG2_VERSION_UNDEFINED\x10\x00\x12\x17\n\x13MUSIG2_VERSION_V040\x10\x01\x12\x1a\n\x16MUSIG2_VERSION_V100RC2\x10\x02\x32\xdb\x06\n\x06Signer\x12\x34\n\rSignOutputRaw\x12\x10.signrpc.SignReq\x1a\x11.signrpc.SignResp\x12@\n\x12\x43omputeInputScript\x12\x10.signrpc.SignReq\x1a\x18.signrpc.InputScriptResp\x12@\n\x0bSignMessage\x12\x17.signrpc.SignMessageReq\x1a\x18.signrpc.SignMessageResp\x12\x46\n\rVerifyMessage\x12\x19.signrpc.VerifyMessageReq\x1a\x1a.signrpc.VerifyMessageResp\x12H\n\x0f\x44\x65riveSharedKey\x12\x19.signrpc.SharedKeyRequest\x1a\x1a.signrpc.SharedKeyResponse\x12Z\n\x11MuSig2CombineKeys\x12!.signrpc.MuSig2CombineKeysRequest\x1a\".signrpc.MuSig2CombineKeysResponse\x12T\n\x13MuSig2CreateSession\x12\x1d.signrpc.MuSig2SessionRequest\x1a\x1e.signrpc.MuSig2SessionResponse\x12\x63\n\x14MuSig2RegisterNonces\x12$.signrpc.MuSig2RegisterNoncesRequest\x1a%.signrpc.MuSig2RegisterNoncesResponse\x12\x45\n\nMuSig2Sign\x12\x1a.signrpc.MuSig2SignRequest\x1a\x1b.signrpc.MuSig2SignResponse\x12W\n\x10MuSig2CombineSig\x12 .signrpc.MuSig2CombineSigRequest\x1a!.signrpc.MuSig2CombineSigResponse\x12N\n\rMuSig2Cleanup\x12\x1d.signrpc.MuSig2CleanupRequest\x1a\x1e.signrpc.MuSig2CleanupResponseB/Z-github.com/lightningnetwork/lnd/lnrpc/signrpcb\x06proto3')

_globals = globals()
_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, _globals)
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'signer_pb2', _globals)
if not _descriptor._USE_C_DESCRIPTORS:
  _globals['DESCRIPTOR']._loaded_options = None
  _globals['DESCRIPTOR']._serialized_options = b'Z-github.com/lightningnetwork/lnd/lnrpc/signrpc'
  _globals['_SHAREDKEYREQUEST'].fields_by_name['key_loc']._loaded_options = None
  _globals['_SHAREDKEYREQUEST'].fields_by_name['key_loc']._serialized_options = b'\030\001'
  _globals['_SIGNMETHOD']._serialized_start=2662
  _globals['_SIGNMETHOD']._serialized_end=2818
  _globals['_MUSIG2VERSION']._serialized_start=2820
  _globals['_MUSIG2VERSION']._serialized_end=2918
  _globals['_KEYLOCATOR']._serialized_start=25
  _globals['_KEYLOCATOR']._serialized_end=76
  _globals['_KEYDESCRIPTOR']._serialized_start=78
  _globals['_KEYDESCRIPTOR']._serialized_end=154
  _globals['_TXOUT']._serialized_start=156
  _globals['_TXOUT']._serialized_end=197
  _globals['_SIGNDESCRIPTOR']._serialized_start=200
  _globals['_SIGNDESCRIPTOR']._serialized_end=457
  _globals['_SIGNREQ']._serialized_start=459
  _globals['_SIGNREQ']._serialized_end=573
  _globals['_SIGNRESP']._serialized_start=575
  _globals['_SIGNRESP']._serialized_end=603
  _globals['_INPUTSCRIPT']._serialized_start=605
  _globals['_INPUTSCRIPT']._serialized_end=655
  _globals['_INPUTSCRIPTRESP']._serialized_start=657
  _globals['_INPUTSCRIPTRESP']._serialized_end=719
  _globals['_SIGNMESSAGEREQ']._serialized_start=722
  _globals['_SIGNMESSAGEREQ']._serialized_end=896
  _globals['_SIGNMESSAGERESP']._serialized_start=898
  _globals['_SIGNMESSAGERESP']._serialized_end=934
  _globals['_VERIFYMESSAGEREQ']._serialized_start=936
  _globals['_VERIFYMESSAGEREQ']._serialized_end=1039
  _globals['_VERIFYMESSAGERESP']._serialized_start=1041
  _globals['_VERIFYMESSAGERESP']._serialized_end=1075
  _globals['_SHAREDKEYREQUEST']._serialized_start=1078
  _globals['_SHAREDKEYREQUEST']._serialized_end=1206
  _globals['_SHAREDKEYRESPONSE']._serialized_start=1208
  _globals['_SHAREDKEYRESPONSE']._serialized_end=1247
  _globals['_TWEAKDESC']._serialized_start=1249
  _globals['_TWEAKDESC']._serialized_end=1294
  _globals['_TAPROOTTWEAKDESC']._serialized_start=1296
  _globals['_TAPROOTTWEAKDESC']._serialized_end=1359
  _globals['_MUSIG2COMBINEKEYSREQUEST']._serialized_start=1362
  _globals['_MUSIG2COMBINEKEYSREQUEST']._serialized_end=1543
  _globals['_MUSIG2COMBINEKEYSRESPONSE']._serialized_start=1545
  _globals['_MUSIG2COMBINEKEYSRESPONSE']._serialized_end=1665
  _globals['_MUSIG2SESSIONREQUEST']._serialized_start=1668
  _globals['_MUSIG2SESSIONREQUEST']._serialized_end=1953
  _globals['_MUSIG2SESSIONRESPONSE']._serialized_start=1956
  _globals['_MUSIG2SESSIONRESPONSE']._serialized_end=2146
  _globals['_MUSIG2REGISTERNONCESREQUEST']._serialized_start=2148
  _globals['_MUSIG2REGISTERNONCESREQUEST']._serialized_end=2233
  _globals['_MUSIG2REGISTERNONCESRESPONSE']._serialized_start=2235
  _globals['_MUSIG2REGISTERNONCESRESPONSE']._serialized_end=2290
  _globals['_MUSIG2SIGNREQUEST']._serialized_start=2292
  _globals['_MUSIG2SIGNREQUEST']._serialized_end=2372
  _globals['_MUSIG2SIGNRESPONSE']._serialized_start=2374
  _globals['_MUSIG2SIGNRESPONSE']._serialized_end=2427
  _globals['_MUSIG2COMBINESIGREQUEST']._serialized_start=2429
  _globals['_MUSIG2COMBINESIGREQUEST']._serialized_end=2508
  _globals['_MUSIG2COMBINESIGRESPONSE']._serialized_start=2510
  _globals['_MUSIG2COMBINESIGRESPONSE']._serialized_end=2590
  _globals['_MUSIG2CLEANUPREQUEST']._serialized_start=2592
  _globals['_MUSIG2CLEANUPREQUEST']._serialized_end=2634
  _globals['_MUSIG2CLEANUPRESPONSE']._serialized_start=2636
  _globals['_MUSIG2CLEANUPRESPONSE']._serialized_end=2659
  _globals['_SIGNER']._serialized_start=2921
  _globals['_SIGNER']._serialized_end=3780
# @@protoc_insertion_point(module_scope)
