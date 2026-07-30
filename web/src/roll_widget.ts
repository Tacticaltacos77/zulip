import $ from "jquery";
import assert from "minimalistic-assert";

import render_message_hidden_dialog from "../templates/message_hidden_dialog.hbs";
import render_widgets_roll_widget from "../templates/widgets/roll_widget.hbs";
import render_widgets_roll_widget_results from "../templates/widgets/roll_widget_results.hbs";

import * as blueslip from "./blueslip.ts";
import type {Message} from "./message_store.ts";
import * as people from "./people.ts";
import type {RollWidgetOutboundData} from "./roll_data.ts";
import {RollData, new_roll_schema} from "./roll_data.ts";
import {ZulipWidgetContext} from "./widget_context.ts";
import type {Event} from "./widget_data.ts";
import type {AnyWidgetData, WidgetData} from "./widget_schema.ts";


export function activate({any_data, message: _message}: {any_data: AnyWidgetData; message: Message}): {
    inbound_events_handler: (events: Event[]) => void;
    widget_data: WidgetData;
} {

    assert(any_data.widget_type === "roll");
    const {extra_data} = any_data;

    const roll_data = new RollData({
        range: extra_data.range ?? 2,
        count: extra_data.count ?? 1,
        current_user_id: people.my_current_user_id(),
        report_error_function: blueslip.warn,
        get_full_name: people.get_display_full_name,
    });

    const widget_data = {
        widget_type: any_data.widget_type,
        data: roll_data,
    };

    function update_state_from_event(sender_id: number, data: unknown): void {
        assert(
            typeof data === "object" &&
                data !== null &&
                "type" in data &&
                typeof data.type === "string",
        );
        const type = data.type;
        switch (type) {
            case "new_roll": {
                roll_data.handle_new_roll_event(sender_id, new_roll_schema.parse(data));
                break;
            }
            default: {
                blueslip.warn(`roll widget: unknown inbound type: ${type}`);
            }
        }
    }

    function handle_events(events: Event[]): void {
        for (const event of events) {
            update_state_from_event(event.sender_id, event.data);
        }
    }

    return {
        inbound_events_handler: handle_events,
        widget_data,
    };
}

export function render({
    $elem,
    callback,
    message,
    widget_data,
    rerender,
}: {
    $elem: JQuery;
    callback: (data: RollWidgetOutboundData) => void;
    message: Message;
    widget_data: WidgetData;
    rerender: boolean;
}): void {
    assert(widget_data.widget_type === "roll");
    const roll_data = widget_data.data;
    const widget_context = new ZulipWidgetContext(message);
    const container_is_hidden = widget_context.is_container_hidden();

    function update_roll_btn(): void {
        // Disables/enables button based on if a request was sent 
        $elem.find(".roll-button").prop("disabled", roll_data.roll_in_transit)
    }

    function submit_roll(): void {
        const data = roll_data.new_roll_event();
        update_roll_btn();
        if(data){
            callback(data);
        }
    }


    function build_widget(): void {
        const html = render_widgets_roll_widget({count: roll_data.count, range: roll_data.range});
        $elem.html(html);

        $elem.find(".roll-button").on("click", (e) => {
            e.stopPropagation();
            submit_roll();
        });
    }
    
    function render_results(): void {
        const widget_data = roll_data.get_widget_data();
        
        const html = render_widgets_roll_widget_results(widget_data);
        $elem.find(".roll-results-list").html(html);
    }

    if (container_is_hidden) {
        if (!rerender) {
            const html = render_message_hidden_dialog();
            $elem.html(html);
        }
        return;
    }

    if (!rerender) {
        build_widget();
    }

    render_results();
    update_roll_btn();
    return;
}
