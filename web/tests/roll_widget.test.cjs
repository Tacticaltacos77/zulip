"use strict";

const assert = require("node:assert/strict");

const {make_realm} = require("./lib/example_realm.cjs");
const {make_user} = require("./lib/example_user.cjs");
const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const blueslip = require("./lib/zblueslip.cjs");
const {$} = require("./lib/zjquery.cjs");

mock_esm("../src/settings_data", {
    user_can_access_all_other_users: () => true,
});

const {RollData} = zrequire("roll_data");

const roll_widget = zrequire("roll_widget");

const people = zrequire("people");
const {set_realm} = zrequire("state_data");

set_realm(make_realm());

const me = make_user({
    email: "me@zulip.com",
    full_name: "Me Myself",
    user_id: 99,
});
const alice = make_user({
    email: "alice@zulip.com",
    full_name: "Alice Lee",
    user_id: 100,
});
people.add_active_user(me);
people.add_active_user(alice);
people.initialize_current_user(me.user_id);

run_test("RollData rolls", () => {
    const range = 6;
    const count = 2;

    const data_holder = new RollData({
        range,
        count,
        report_error_function: blueslip.warn,
        get_full_name: people.get_display_full_name,
    });

    let data = data_holder.get_widget_data();

    assert.deepEqual(data, {
        range: 6,
        count: 2,
        rolls: [],
    });

    const roll_outbound_event = data_holder.new_roll_event();
    assert.deepEqual(roll_outbound_event, {type: "new_roll"});

    // The server computes the actual dice values/total and
    // broadcasts them back down as a "new_roll" submessage.
    const first_roll_event = {
        type: "new_roll",
        values: [3, 5],
        total: 8,
    };

    data_holder.handle_new_roll_event(me.user_id, first_roll_event);
    data = data_holder.get_widget_data();

    assert.deepEqual(data, {
        range: 6,
        count: 2,
        rolls: [
            {
                values: [3, 5],
                total: 8,
                name: "Me Myself",
            },
        ],
    });

    const second_roll_event = {
        type: "new_roll",
        values: [1, 6],
        total: 7,
    };

    data_holder.handle_new_roll_event(alice.user_id, second_roll_event);
    data = data_holder.get_widget_data();

    assert.deepEqual(data, {
        range: 6,
        count: 2,
        rolls: [
            {
                values: [3, 5],
                total: 8,
                name: "Me Myself",
            },
            {
                values: [1, 6],
                total: 7,
                name: "Alice Lee",
            },
        ],
    });
});

run_test("roll widget: unknown inbound event type", () => {
    const activate_opts = {
        message: {
            sender_id: alice.user_id,
        },
        any_data: {
            widget_type: "roll",
            extra_data: {
                range: 6,
                count: 2,
            },
        },
    };

    const {inbound_events_handler} = roll_widget.activate(activate_opts);

    blueslip.expect("warn", "roll widget: unknown inbound type: bogus");
    inbound_events_handler([
        {
            sender_id: alice.user_id,
            data: {type: "bogus"},
        },
    ]);
});

run_test("activate and render roll widget", ({mock_template}) => {
    mock_template("widgets/roll_widget.hbs", false, () => "widgets/roll_widget");

    // Capture the data passed to the results template so we can
    // confirm rolls make it all the way from an inbound event to
    // what would get rendered, including the sender's resolved name.
    let rendered_widget_data;
    mock_template("widgets/roll_widget_results.hbs", false, (data) => {
        rendered_widget_data = data;
        return "widgets/roll_widget_results";
    });

    const $widget_elem = $("<div>").addClass("widget-content");

    let out_data; // Used to check the event data sent to the server
    const callback = (data) => {
        out_data = data;
    };

    const activate_opts = {
        message: {
            sender_id: alice.user_id,
        },
        any_data: {
            widget_type: "roll",
            extra_data: {
                range: 6,
                count: 2,
            },
        },
    };

    const set_widget_find_result = (selector) => {
        const $elem = $.create(selector);
        $widget_elem.set_find_results(selector, $elem);
        return $elem;
    };

    const $roll_button = set_widget_find_result(".roll-button");
    const $roll_results_list = set_widget_find_result(".roll-results-list");

    const {inbound_events_handler, widget_data} = roll_widget.activate(activate_opts);
    const render_opts = {
        $elem: $widget_elem,
        callback,
        message: {
            sender_id: alice.user_id,
        },
        widget_data,
        rerender: false,
    };

    roll_widget.render(render_opts);

    assert.equal($widget_elem.html(), "widgets/roll_widget");
    assert.equal($roll_results_list.html(), "widgets/roll_widget_results");
    assert.deepEqual(rendered_widget_data, {range: 6, count: 2, rolls: []});
    assert.ok(!$roll_button.prop("disabled"));

    {
        /* Testing data sent to server on clicking "Roll" */
        out_data = undefined;
        $roll_button.trigger("click");
        assert.deepEqual(out_data, {type: "new_roll"});
        // The button is disabled while our roll is in transit, so
        // that we can't spam the server with duplicate requests.
        assert.ok($roll_button.prop("disabled"));
    }

    const alice_roll_events = [
        {
            sender_id: alice.user_id,
            data: {
                type: "new_roll",
                values: [4, 2],
                total: 6,
            },
        },
    ];

    inbound_events_handler(alice_roll_events);
    roll_widget.render({...render_opts, rerender: true});

    assert.deepEqual(rendered_widget_data, {
        range: 6,
        count: 2,
        rolls: [
            {
                values: [4, 2],
                total: 6,
                name: "Alice Lee",
            },
        ],
    });
    // Someone else's roll coming in shouldn't re-enable our button;
    // we're still waiting on the server to confirm our own roll.
    assert.ok($roll_button.prop("disabled"));

    const my_roll_events = [
        {
            sender_id: me.user_id,
            data: {
                type: "new_roll",
                values: [1, 1],
                total: 2,
            },
        },
    ];

    inbound_events_handler(my_roll_events);
    roll_widget.render({...render_opts, rerender: true});

    // Once the server confirms our own roll, the button is
    // enabled so we can roll again.
    assert.ok(!$roll_button.prop("disabled"));
});
