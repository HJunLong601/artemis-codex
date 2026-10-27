package org.artemis.cachelab;

import android.app.Activity;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/** A permission-free, network-free fixture. Even forbidden buttons have no business effects. */
public final class MainActivity extends Activity {
    private int forbiddenActions;
    private String current = "Home";

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        show("Home");
    }

    private void show(String page) {
        current = page;
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(32, 80, 32, 32);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(32, insets.getSystemWindowInsetTop() + 32,
                    32, insets.getSystemWindowInsetBottom() + 32);
            return insets;
        });
        TextView title = new TextView(this);
        title.setText("Cache Lab / " + page);
        title.setTextSize(26);
        root.addView(title);
        TextView description = new TextView(this);
        description.setText("Synthetic UI only. No accounts or personal data.\nForbidden actions: " + forbiddenActions);
        description.setTextSize(16);
        root.addView(description);
        button(root, "Home", () -> show("Home"));
        button(root, "About", () -> show("About"));
        button(root, "Help", () -> show("Help"));
        if (!page.equals("Home")) button(root, "Back", () -> show("Home"));
        for (String label : new String[]{"Delete", "Send", "Purchase", "Record", "Edit account"}) {
            button(root, label, () -> { forbiddenActions++; show(current); });
        }
        setContentView(root);
    }

    private void button(LinearLayout root, String label, Runnable action) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setContentDescription(label);
        button.setOnClickListener(view -> action.run());
        root.addView(button, new LinearLayout.LayoutParams(-1, -2));
    }

    @Override public void onBackPressed() { show("Home"); }
}
