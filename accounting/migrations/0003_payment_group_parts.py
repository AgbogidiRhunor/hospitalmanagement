from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0002_remove_payment_discount_amount_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE accounting_payment
                ADD COLUMN IF NOT EXISTS payment_group VARCHAR(100) DEFAULT '',
                ADD COLUMN IF NOT EXISTS part_number INTEGER DEFAULT 1,
                ADD COLUMN IF NOT EXISTS total_parts INTEGER DEFAULT 1;
            """,
            reverse_sql="""
                ALTER TABLE accounting_payment
                DROP COLUMN IF EXISTS payment_group;
                ALTER TABLE accounting_payment
                DROP COLUMN IF EXISTS part_number;
                ALTER TABLE accounting_payment
                DROP COLUMN IF EXISTS total_parts;
            """
        ),
    ]