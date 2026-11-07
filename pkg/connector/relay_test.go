package connector

import (
	"context"
	"database/sql"
	"errors"
	"testing"

	_ "github.com/mattn/go-sqlite3"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"go.mau.fi/util/dbutil"
	"gopkg.in/yaml.v3"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/bridgeconfig"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/event"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/matrixfmt"
	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
)

type relayInvoker func(context.Context, bin.Encoder, bin.Decoder) error

func (invoke relayInvoker) Invoke(ctx context.Context, input bin.Encoder, output bin.Decoder) error {
	return invoke(ctx, input, output)
}

func relayFixture(t *testing.T) (*TelegramClient, *bridgev2.MatrixMessage, *tg.MessagesSendMediaRequest) {
	t.Helper()
	connection, err := sql.Open("sqlite3", ":memory:")
	require.NoError(t, err)
	connection.SetMaxOpenConns(1)
	t.Cleanup(func() { require.NoError(t, connection.Close()) })
	// Exercise the production message queries without requiring unrelated
	// bridge accounts, encryption tables or live Matrix services.
	_, err = connection.Exec(`CREATE TABLE message (
		bridge_id TEXT, id TEXT, part_id TEXT, mxid TEXT, room_id TEXT, room_receiver TEXT,
		sender_id TEXT, sender_mxid TEXT, timestamp BIGINT, edit_count INTEGER,
		double_puppeted BOOLEAN, thread_root_id TEXT, reply_to_id TEXT, reply_to_part_id TEXT,
		send_txn_id TEXT, metadata TEXT,
		UNIQUE (bridge_id, room_receiver, id, part_id)
	)`)
	require.NoError(t, err)
	rawDB, err := dbutil.NewWithDB(connection, "sqlite3")
	require.NoError(t, err)
	db := database.New("test", database.MetaTypes{Message: func() any { return &MessageMetadata{} }}, rawDB)
	config := &bridgeconfig.BridgeConfig{}
	require.NoError(t, yaml.Unmarshal([]byte(`displayname_format: '{{ .DisambiguatedName }}'
message_formats:
  m.text: '<b>{{ .Sender.DisambiguatedName }}</b>: {{ .Message }}'
  m.image: '<b>{{ .Sender.DisambiguatedName }}</b> sent an image{{ if .Caption }}: {{ .Caption }}{{ end }}'
`), &config.Relay))
	bridge := &bridgev2.Bridge{DB: db, Config: config}
	tc := &TelegramClient{main: &TelegramConnector{Bridge: bridge}, userID: "123"}
	portal := &bridgev2.Portal{Bridge: bridge, Portal: &database.Portal{PortalKey: networkid.PortalKey{ID: "chat:5", Receiver: "123"}}}
	msg := &bridgev2.MatrixMessage{MatrixEventBase: bridgev2.MatrixEventBase[*event.MessageEventContent]{
		Event:      &event.Event{ID: "$sticker", Sender: "@alice:example.org", Type: event.EventSticker},
		Content:    &event.MessageEventContent{Body: "🙂", URL: "mxc://example.org/sticker", MsgType: event.CapMsgSticker},
		OrigSender: &bridgev2.OrigSender{DisambiguatedName: "Alice", UserID: "@alice:example.org"},
		Portal:     portal,
	}}
	req := &tg.MessagesSendMediaRequest{
		Peer:    &tg.InputPeerChat{ChatID: 5},
		ReplyTo: &tg.InputReplyToMessage{ReplyToMsgID: 17, TopMsgID: 10},
		Message: "🙂",
		Media: &tg.InputMediaUploadedDocument{MimeType: "image/webp", Attributes: []tg.DocumentAttributeClass{
			&tg.DocumentAttributeSticker{Alt: "🙂", Stickerset: &tg.InputStickerSetEmpty{}},
		}},
	}
	return tc, msg, req
}

func relayUpdates(output bin.Decoder, messageID int) {
	output.(*tg.UpdatesBox).Updates = &tg.UpdateShortSentMessage{ID: messageID, Date: 1700000000}
}

func TestRelayStickerRequestsAndEchoMapping(t *testing.T) {
	tc, msg, req := relayFixture(t)
	var randomIDs []int64
	var calls []string
	api := tg.NewClient(relayInvoker(func(_ context.Context, input bin.Encoder, output bin.Decoder) error {
		switch request := input.(type) {
		case *tg.MessagesSendMessageRequest:
			calls = append(calls, "attribution")
			require.Equal(t, "Alice", request.Message)
			require.True(t, request.NoWebpage)
			require.Equal(t, req.ReplyTo, request.ReplyTo)
			randomIDs = append(randomIDs, request.RandomID)
			relayUpdates(output, 21)
		case *tg.MessagesSendMediaRequest:
			calls = append(calls, "sticker")
			require.Empty(t, request.Message)
			require.Empty(t, request.Entities)
			require.Equal(t, req.Peer, request.Peer)
			require.Equal(t, &tg.InputReplyToMessage{ReplyToMsgID: 17, TopMsgID: 10}, request.ReplyTo)
			media := request.Media.(*tg.InputMediaUploadedDocument)
			require.IsType(t, &tg.DocumentAttributeSticker{}, media.Attributes[0])
			randomIDs = append(randomIDs, request.RandomID)
			// The exact remote-ID mapping must exist before the sticker send,
			// so a queued attribution echo cannot create an extra Matrix event.
			aux, err := tc.main.Bridge.DB.Message.GetPartByID(t.Context(), msg.Portal.Receiver, "21", "")
			require.NoError(t, err)
			require.NotNil(t, aux)
			require.True(t, aux.HasFakeMXID())
			require.NotEqual(t, msg.Event.ID, aux.MXID)
			relayUpdates(output, 22)
		default:
			t.Fatalf("unexpected RPC %T", request)
		}
		return nil
	}))
	for range 2 {
		resp, err := tc.sendRelaySticker(t.Context(), api, msg, req, func() (tg.InputMediaClass, error) {
			t.Fatal("unexpected media refresh")
			return nil, nil
		})
		require.NoError(t, err)
		require.Equal(t, networkid.MessageID("22"), resp.DB.ID)
		require.Equal(t, 21, resp.DB.Metadata.(*MessageMetadata).RelayAttributionID)
		require.Equal(t, msg.Content.URL, resp.DB.Metadata.(*MessageMetadata).ContentURI)
	}
	require.Equal(t, []string{"attribution", "sticker", "sticker"}, calls)
	require.NotZero(t, randomIDs[0])
	require.NotZero(t, randomIDs[1])
	require.NotEqual(t, randomIDs[0], randomIDs[1])
	require.Equal(t, randomIDs[1], randomIDs[2], "retrying must reuse the sticker random ID and known attribution")
	other, _ := relayStickerRandomIDs("$different-sticker")
	require.NotEqual(t, randomIDs[1], other)
}

func TestRelayStickerAttributionFailureStopsSend(t *testing.T) {
	tc, msg, req := relayFixture(t)
	calls := 0
	api := tg.NewClient(relayInvoker(func(_ context.Context, input bin.Encoder, _ bin.Decoder) error {
		calls++
		require.IsType(t, &tg.MessagesSendMessageRequest{}, input)
		return errors.New("attribution rejected")
	}))
	resp, err := tc.sendRelaySticker(t.Context(), api, msg, req, nil)
	require.Error(t, err)
	require.Nil(t, resp)
	require.Equal(t, 1, calls, "do not send an unattributed sticker after a failed attribution")
}

func TestRelayStickerFailureRemovesAttribution(t *testing.T) {
	for _, cleanupFails := range []bool{false, true} {
		t.Run(map[bool]string{false: "cleanup succeeds", true: "cleanup fails visibly"}[cleanupFails], func(t *testing.T) {
			tc, msg, req := relayFixture(t)
			var calls []string
			api := tg.NewClient(relayInvoker(func(_ context.Context, input bin.Encoder, output bin.Decoder) error {
				switch request := input.(type) {
				case *tg.MessagesSendMessageRequest:
					calls = append(calls, "attribution")
					relayUpdates(output, 21)
				case *tg.MessagesSendMediaRequest:
					calls = append(calls, "sticker")
					return errors.New("sticker rejected")
				case *tg.MessagesDeleteMessagesRequest:
					calls = append(calls, "cleanup")
					require.Equal(t, []int{21}, request.ID)
					require.True(t, request.Revoke)
					if cleanupFails {
						return errors.New("deletion rejected")
					}
				default:
					t.Fatalf("unexpected RPC %T", request)
				}
				return nil
			}))
			resp, err := tc.sendRelaySticker(t.Context(), api, msg, req, nil)
			require.Error(t, err)
			require.Nil(t, resp)
			require.Equal(t, []string{"attribution", "sticker", "cleanup"}, calls)
			aux, lookupErr := tc.main.Bridge.DB.Message.GetPartByID(t.Context(), msg.Portal.Receiver, "21", "")
			require.NoError(t, lookupErr)
			if cleanupFails {
				require.ErrorContains(t, err, "failed to remove relay attribution 21")
				require.NotNil(t, aux, "retain exact-ID echo suppression when remote cleanup failed")
			} else {
				require.Nil(t, aux)
			}
		})
	}
}

func TestRelayStickerTrackingFailureStopsAndCleansUp(t *testing.T) {
	tc, msg, req := relayFixture(t)
	_, err := tc.main.Bridge.DB.Exec(t.Context(), `CREATE TRIGGER reject_message BEFORE INSERT ON message
		BEGIN SELECT RAISE(FAIL, 'cannot track message'); END`)
	require.NoError(t, err)
	var calls []string
	api := tg.NewClient(relayInvoker(func(_ context.Context, input bin.Encoder, output bin.Decoder) error {
		switch request := input.(type) {
		case *tg.MessagesSendMessageRequest:
			calls = append(calls, "attribution")
			relayUpdates(output, 21)
		case *tg.MessagesDeleteMessagesRequest:
			calls = append(calls, "cleanup")
			require.Equal(t, []int{21}, request.ID)
		default:
			t.Fatalf("untracked attribution must not be followed by a sticker: %T", request)
		}
		return nil
	}))
	resp, err := tc.sendRelaySticker(t.Context(), api, msg, req, nil)
	require.ErrorContains(t, err, "failed to track relay attribution")
	require.Nil(t, resp)
	require.Equal(t, []string{"attribution", "cleanup"}, calls)
}

func TestRelayStickerExpiredReferenceRetriesOnlyMedia(t *testing.T) {
	tc, msg, req := relayFixture(t)
	var calls []string
	var mediaRandomID int64
	api := tg.NewClient(relayInvoker(func(_ context.Context, input bin.Encoder, output bin.Decoder) error {
		switch request := input.(type) {
		case *tg.MessagesSendMessageRequest:
			calls = append(calls, "attribution")
			relayUpdates(output, 21)
		case *tg.MessagesSendMediaRequest:
			calls = append(calls, "sticker")
			if mediaRandomID == 0 {
				mediaRandomID = request.RandomID
				return tgerr.New(400, tg.ErrFileReferenceExpired)
			}
			require.Equal(t, mediaRandomID, request.RandomID)
			relayUpdates(output, 22)
		default:
			t.Fatalf("unexpected RPC %T", request)
		}
		return nil
	}))
	_, err := tc.sendRelaySticker(t.Context(), api, msg, req, func() (tg.InputMediaClass, error) {
		calls = append(calls, "refresh")
		return req.Media, nil
	})
	require.NoError(t, err)
	require.Equal(t, []string{"attribution", "sticker", "refresh", "sticker"}, calls)
}

func TestRelayFormattingPreservesMediaCaptionAndFilename(t *testing.T) {
	tc, msg, _ := relayFixture(t)
	for _, test := range []struct {
		name           string
		input          *event.MessageEventContent
		body, filename string
	}{
		{"text", &event.MessageEventContent{MsgType: event.MsgText, Body: "hello"}, "Alice: hello", ""},
		{"image", &event.MessageEventContent{MsgType: event.MsgImage, Body: "photo.png"}, "Alice sent an image", "photo.png"},
		{"caption", &event.MessageEventContent{MsgType: event.MsgImage, Body: "look", FileName: "photo.png"}, "Alice sent an image: look", "photo.png"},
	} {
		t.Run(test.name, func(t *testing.T) {
			original := *test.input
			formatted, err := tc.formatRelayMessage(test.input, msg.OrigSender)
			require.NoError(t, err)
			text, _ := matrixfmt.Parse(t.Context(), &matrixfmt.HTMLParser{}, formatted, msg.Portal, 4096)
			require.Equal(t, test.body, text)
			require.Equal(t, test.filename, formatted.FileName)
			require.Equal(t, original, *test.input, "formatting must not mutate the original event")
			unrelayed, err := tc.formatRelayMessage(test.input, nil)
			require.NoError(t, err)
			require.Same(t, test.input, unrelayed)
		})
	}
}

func TestRelayRedactionDeletesStickerAndAttribution(t *testing.T) {
	for _, channel := range []bool{false, true} {
		t.Run(map[bool]string{false: "chat", true: "channel"}[channel], func(t *testing.T) {
			tc, msg, _ := relayFixture(t)
			var peer tg.InputPeerClass = &tg.InputPeerChat{ChatID: 5}
			if channel {
				peer = &tg.InputPeerChannel{ChannelID: 5, AccessHash: 9}
				msg.Portal.ID = ids.MakePortalID(ids.PeerTypeChannel, 5)
			}
			aux := &database.Message{ID: ids.MakeMessageID(msg.Portal.PortalKey, 21), Room: msg.Portal.PortalKey, SenderID: tc.userID}
			aux.SetFakeMXID()
			require.NoError(t, tc.main.Bridge.DB.Message.Insert(t.Context(), aux))
			message := &database.Message{ID: ids.MakeMessageID(msg.Portal.PortalKey, 22), Metadata: &MessageMetadata{RelayAttributionID: 21}}
			api := tg.NewClient(relayInvoker(func(_ context.Context, input bin.Encoder, _ bin.Decoder) error {
				if channel {
					req := input.(*tg.ChannelsDeleteMessagesRequest)
					require.Equal(t, []int{22, 21}, req.ID)
					require.Equal(t, &tg.InputChannel{ChannelID: 5, AccessHash: 9}, req.Channel)
				} else {
					req := input.(*tg.MessagesDeleteMessagesRequest)
					require.Equal(t, []int{22, 21}, req.ID)
					require.True(t, req.Revoke)
				}
				return nil
			}))
			require.NoError(t, tc.deleteMappedTelegramMessage(t.Context(), api, msg.Portal, peer, message))
			remaining, err := tc.main.Bridge.DB.Message.GetAllPartsByID(t.Context(), msg.Portal.Receiver, aux.ID)
			require.NoError(t, err)
			require.Empty(t, remaining)
		})
	}
	ordinary, err := relayMessageIDs(&database.Message{ID: "22", Metadata: &MessageMetadata{}})
	require.NoError(t, err)
	require.Equal(t, []int{22}, ordinary)
}

func TestRelayStickerEditsAreRejectedBeforeNetworkCalls(t *testing.T) {
	for _, isStickerEvent := range []bool{false, true} {
		tc, msg, _ := relayFixture(t)
		metadata := &MessageMetadata{RelayAttributionID: 21}
		if !isStickerEvent {
			msg.Event.Type = event.EventMessage
		}
		err := tc.HandleMatrixEdit(t.Context(), &bridgev2.MatrixEdit{
			MatrixEventBase: msg.MatrixEventBase,
			EditTarget:      &database.Message{Metadata: metadata},
		})
		assert.ErrorIs(t, err, bridgev2.ErrEditsNotSupported)
	}
}
