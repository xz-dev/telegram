// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2026 mautrix-telegram contributors
// SPDX-License-Identifier: AGPL-3.0-or-later

package connector

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"fmt"
	"math"

	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/event"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
)

// Separate, stable IDs prevent retries of the two-message relay send from
// producing duplicate attribution messages. Telegram accepts signed random IDs.
func relayStickerRandomIDs(eventID id.EventID) (sticker, attribution int64) {
	hash := sha256.Sum256([]byte("telegram-relay-sticker:" + eventID))
	sticker = int64(binary.LittleEndian.Uint64(hash[:8]) & math.MaxInt64)
	if sticker == 0 {
		sticker = 1
	}
	return sticker, -sticker
}

func (tc *TelegramClient) formatRelayMessage(content *event.MessageEventContent, sender *bridgev2.OrigSender) (*event.MessageEventContent, error) {
	if sender == nil {
		return content, nil
	}
	return tc.main.Bridge.Config.Relay.FormatMessage(content, sender)
}

// The media has already been uploaded before this function causes any messages
// to appear. The sticker remains the sole real Matrix event mapping.
func (tc *TelegramClient) sendRelaySticker(
	ctx context.Context,
	api *tg.Client,
	msg *bridgev2.MatrixMessage,
	req *tg.MessagesSendMediaRequest,
	refetch func() (tg.InputMediaClass, error),
) (*bridgev2.MatrixMessageResponse, error) {
	name := tc.main.Bridge.Config.Relay.FormatName(msg.OrigSender)
	if name == "" {
		name = msg.OrigSender.DisambiguatedName
	}
	if name == "" {
		name = msg.OrigSender.UserID.String()
	}
	if name == "" {
		return nil, fmt.Errorf("relay sticker sender has no usable attribution")
	}
	var attributionRandomID int64
	req.RandomID, attributionRandomID = relayStickerRandomIDs(msg.Event.ID)
	attributionTxnID := networkid.RawTransactionID("relay-attribution:" + msg.Event.ID)
	attributionDB, err := tc.main.Bridge.DB.Message.GetPartByTxnID(ctx, msg.Portal.Receiver, "", attributionTxnID)
	if err != nil {
		return nil, err
	}
	var updates tg.UpdatesClass
	if attributionDB == nil {
		updates, err = api.MessagesSendMessage(ctx, &tg.MessagesSendMessageRequest{
			Peer: req.Peer, ReplyTo: req.ReplyTo, NoWebpage: true,
			Message: name, RandomID: attributionRandomID,
		})
		if err != nil {
			return nil, tc.humaniseSendError(err)
		}
		attribution, _, responseErr := tc.makeSendResponse(ctx, msg.Portal, updates, attributionRandomID, name, "")
		if responseErr != nil {
			return nil, fmt.Errorf("sent relay attribution but could not identify it: %w", responseErr)
		}
		attributionDB = attribution.DB
		// A fake-MXID row suppresses exact remote-ID echoes without mapping a
		// second remote message to the real Matrix event. Retain the input
		// transaction key so a retried send can reuse the known attribution.
		// shortcut: replies to this auxiliary text have no Matrix reply target;
		// full reply mapping needs bridgev2 support for multi-message sends.
		attributionDB.Room = msg.Portal.PortalKey
		attributionDB.SenderMXID = msg.Event.Sender
		attributionDB.SendTxnID = attributionTxnID
		attributionDB.SetFakeMXID()
		err = tc.main.Bridge.DB.Message.Insert(ctx, attributionDB)
	} else if !attributionDB.HasFakeMXID() || attributionDB.Room != msg.Portal.PortalKey {
		return nil, fmt.Errorf("relay attribution transaction belongs to another message")
	}
	_, attributionID, parseErr := ids.ParseMessageID(attributionDB.ID)
	if parseErr != nil {
		return nil, parseErr
	}
	if err != nil {
		return nil, fmt.Errorf("failed to track relay attribution: %w", err)
	}
	// A send error may mean only that the response was lost. Keep the live
	// attribution and its deduplication ID for retry instead of compensating.
	// Stickers have no captions, including their Matrix alt text.
	req.Message, req.Entities = "", nil
	updates, err = api.MessagesSendMedia(ctx, req)
	if tgerr.Is(err, tg.ErrFileReferenceExpired) {
		req.Media, err = refetch()
		if err == nil {
			updates, err = api.MessagesSendMedia(ctx, req)
		}
	}
	if err != nil {
		return nil, tc.humaniseSendError(err)
	}
	uri := msg.Content.URL
	if uri == "" && msg.Content.File != nil {
		uri = msg.Content.File.URL
	}
	resp, _, err := tc.makeSendResponse(ctx, msg.Portal, updates, req.RandomID, msg.Content.Body, uri)
	if err != nil {
		return nil, fmt.Errorf("sent relay sticker but could not identify it: %w", err)
	}
	resp.DB.Metadata.(*MessageMetadata).RelayAttributionID = attributionID
	return resp, nil
}

func deleteTelegramMessages(ctx context.Context, api *tg.Client, peer tg.InputPeerClass, messageIDs []int) error {
	if channel, ok := peer.(*tg.InputPeerChannel); ok {
		_, err := api.ChannelsDeleteMessages(ctx, &tg.ChannelsDeleteMessagesRequest{
			Channel: &tg.InputChannel{ChannelID: channel.ChannelID, AccessHash: channel.AccessHash},
			ID:      messageIDs,
		})
		return err
	}
	_, err := api.MessagesDeleteMessages(ctx, &tg.MessagesDeleteMessagesRequest{Revoke: true, ID: messageIDs})
	return err
}

func (tc *TelegramClient) deleteMappedTelegramMessage(ctx context.Context, api *tg.Client, portal *bridgev2.Portal, peer tg.InputPeerClass, message *database.Message) error {
	messageIDs, err := relayMessageIDs(message)
	if err != nil {
		return err
	}
	if err = deleteTelegramMessages(ctx, api, peer, messageIDs); err != nil {
		return tc.humaniseSendError(err)
	}
	if len(messageIDs) == 2 {
		return tc.main.Bridge.DB.Message.DeleteAllParts(ctx, portal.Receiver, ids.MakeMessageID(portal.PortalKey, messageIDs[1]))
	}
	return nil
}

func relayMessageIDs(message *database.Message) ([]int, error) {
	_, messageID, err := ids.ParseMessageID(message.ID)
	if err != nil {
		return nil, err
	}
	messageIDs := []int{messageID}
	if metadata, ok := message.Metadata.(*MessageMetadata); ok && metadata.RelayAttributionID != 0 {
		messageIDs = append(messageIDs, metadata.RelayAttributionID)
	}
	return messageIDs, nil
}
