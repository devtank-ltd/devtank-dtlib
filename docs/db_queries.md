Devtank Production Tester Database Queries
==========================================

Please reference to the Schema document to digest the many tables and relationships before reading this document.


This document is not intended to provide all queries your ever need, just to get you started.

You will need to join multiple tables in queries for most things.


To move any of these queries from SQLite it is only the time functions to go in and out of UNIX time that needs to change.





Example of listing failure messages in the last day
---------------------------------------------------


When a test fails, the text it failed with is recorded to the database in the values table.

In this example query we are going to get the time a test run was done, the machine it was on, the test group run and the test in that group that failed and finally the text of that failure.

The SQLite query is:


    SELECT datetime(time_of_tests / 1000000, 'unixepoch', 'localtime') AS test_time,
           tester_machines.mac AS tester, example_devs.serial_number AS device, test_groups.name AS group_name,
           test_group_entries.name AS test_name, `values`.value_text AS fail_text
    FROM example_dev_test_results
    JOIN test_group_entries ON test_group_entries.id = group_entry_id
    JOIN test_group_results ON test_group_results.id = example_dev_test_results.group_result_id
    JOIN test_groups ON test_groups.id = test_group_results.group_id
    JOIN example_devs ON example_devs.id = example_dev_id
    JOIN tester_machines on tester_machines.id = tester_machine_id
    JOIN example_dev_test_results_values ON example_dev_test_results_values.test_result_id = example_dev_test_results.id
    JOIN `values` ON `values`.id = example_dev_test_results_values.value_id
    WHERE strftime('%s', 'now', '-1 day') * 1000000 > time_of_tests AND `values`.name LIKE 'SUB_FAIL%'
    ORDER BY test_time


Queries of the results start with the application specific device results table.
To this we join the test group entries to get the name of the tests in in the group and the test group results to get the test session's start time.
Next we want the name of the group so using the test group result table's group ID link in the test groups table.
We also want the serial of the device being tested, so we link in the devices table.
We also want the MAC of the tester used, so we link in the tester machines table.
We want the text of the failure from the values table so we need to first link the table linking results and values tables and then the values table itself.

We aren't interested in all recorded values during the test, only the "SUB_FAIL" kind as that's the name they are recorded under.
Also, we don't want all results ever as the database could be very big, so we just take results of the sessions run over the last day.



Example of listing current test groups
--------------------------------------

Though we probably know the name of the test groups in the database, there may be multiple versions of each each with it's own set of results.

To get just name and ID of the current tests we can do:

    SELECT id, name FROM test_groups WHERE valid_to IS NULL


However, if we want all the versions of a test, called say, "Sunny Day", we could do:


    SELECT id,
       datetime(valid_from / 1000000, 'unixepoch', 'localtime') as valid_from,
       datetime(valid_to / 1000000, 'unixepoch', 'localtime') as valid_to
    FROM test_groups WHERE name="Sunny Day"


If we have the test group ID, say 13, of a test group we know was bad, we can find the devices it was used with by doing:


    SELECT datetime(time_of_tests / 1000000, 'unixepoch', 'localtime') AS test_time,
           serial_number
    FROM test_group_results
    JOIN example_dev_test_results ON example_dev_test_results.group_result_id = test_group_results.id
    JOIN example_devs ON example_devs.id = example_dev_id
    WHERE test_group_results.group_id = 13


Knowing the specific group ID already, we don't need the test_groups table, we can just go straight to the test_group_results with that.
With the test_group_results table we can then join the device results table example_dev_test_results table.
Finally we can then join the devices table to result from device ID to serial number. Or anything project specific in that version of the devices table.



Example of specific device history
----------------------------------


Say we have a board/device we know the serial and we want to get the history of it from the DB.
Here's a quick query to find the history of BOARD_66613.


    SELECT datetime(time_of_tests / 1000000, 'unixepoch', 'localtime') AS test_time,
           test_groups.name,
           test_group_entries.name,
           example_dev_test_results.pass_fail
    FROM example_devs
    JOIN example_dev_test_results ON example_dev_test_results.example_dev_id = example_devs.id
    JOIN test_group_results ON test_group_results.id = example_dev_test_results.group_result_id
    JOIN test_groups ON test_groups.id = test_group_results.group_id
    JOIN test_group_entries ON test_group_entries.id = example_dev_test_results.group_entry_id
    WHERE serial_number="BOARD_66613"

We start spinning up our query from the devices table.
We join to that all the device results.
So we have the test run times and test group ID, we link in the group results table.
To get test group name from the ID we link in the test group table.
To get the test names we link in the test groups entry table.

This gives us the names and times and pass or fail of the tests this board has had.



Example of test duration times
------------------------------

Some times test times aren't deterministic as there is waits for external factors.
To find out what the average time taken by all the tests are we could simply do:


    SELECT test_groups.name AS group_name,
           test_group_entries.name AS test_name,
           AVG(example_dev_test_results.duration) / 1000000 AS seconds
    FROM example_dev_test_results
    JOIN test_group_results ON test_group_results.id = example_dev_test_results.group_result_id
    JOIN test_groups ON test_groups.id = test_group_results.group_id
    JOIN test_group_entries ON test_group_entries.id = example_dev_test_results.group_entry_id
    GROUP BY group_name, test_name

However, this may not be useful as it's the average over all time.
We'd be better asking for an average over a specific period of time. We could of course do that with a WHERE before the GROUP BY to frame the time we are interested in:

   WHERE time_of_tests > strftime('%s', '2004-01-01 13:00:00') * 1000000 AND time_of_tests < strftime('%s', '2034-01-01 13:00:00') * 1000000



Example of test pass rates
--------------------------

To get the percentage a test passes when it is run you can simply do:

    SELECT test_groups.name AS group_name,
           test_group_entries.name AS test_name,
           AVG(example_dev_test_results.pass_fail) * 100 AS pass_rate
    FROM example_dev_test_results
    JOIN test_group_results ON test_group_results.id = example_dev_test_results.group_result_id
    JOIN test_groups ON test_groups.id = test_group_results.group_id
    JOIN test_group_entries ON test_group_entries.id = example_dev_test_results.group_entry_id
    GROUP BY group_name, test_name

As it's very similar to above, you can average over a specific time window in the same way.



Extracting a value stored by test runs
--------------------------------------


If you have a specific value taken during testing stored to the database, say "3V3_Rail" and want to see the average of all of the reading of it over the last day, you can do the query:


    SELECT AVG(`values`.value_real) AS voltage
    FROM example_dev_test_results
    JOIN test_group_results ON test_group_results.id = example_dev_test_results.group_result_id
    JOIN example_dev_test_results_values ON example_dev_test_results_values.test_result_id = example_dev_test_results.id
    JOIN `values` ON `values`.id = example_dev_test_results_values.value_id
    WHERE strftime('%s', 'now', '-1 day') * 1000000 > time_of_tests AND `values`.name = '3V3_Rail'

Like before we start with the device results tables, join the test group results table for the session time, then join the table joining device results and values, then finally the values table.
We are only interested in "3V3_Rail" and entries over the last day so that's what goes in the WHERE statement.



Grafana queries
---------------

So you have a test called "rail_readings.py" and it reads three power rails, '3V3_Rail', '5V_Rail' and '12V_Rail' and you would like to graph that with Grafana.
Well first off, you we are going to assume you are using a MySQL backend not a SQLite for this, but it's broadly what we have already done before.

The key difference is the different UNIX time function and using the Grafana time variables, "$__from", "$__to" and  "$__interval_ms" all in milliseconds.


    SELECT FROM_UNIXTIME((time_of_tests - (time_of_tests % ($__interval_ms * 1000))) / 1000000) as 'time', AVG(value_real) AS "Mean of", `values`.name AS value_name FROM test_group_results
    JOIN example_dev_test_results        ON example_dev_test_results.group_result_id       = test_group_results.id
    JOIN example_dev_test_results_values ON example_dev_test_results_values.test_result_id = example_dev_test_results.id
    JOIN `values`                        ON `values`.id                                    = example_dev_test_results_values.value_id
    JOIN test_group_entries              ON test_group_entries.id                          = example_dev_test_results.group_entry_id
    WHERE time_of_tests >= ($__from * 1000) AND time_of_tests < ($__to * 1000) AND
          test_group_entries.name = 'rail_readings.py' AND `values`.name IN ('3V3_Rail', '5V_Rail', '12V_Rail')
    GROUP BY time, value_name
    ORDER BY time

The three columns returned Grafana knows to use as time, value and name. As a bonus, it will take the value column and prepend it to the name column for us.

We subtract how much the time is over the interval to bring it down to the interval boundary before coverting to a time stamp. We group by that time and the value name to get the average.
Unlike in previous queries, here we only want the reading for these values from a specific test, so we join in test group entries so we can filter by it's name "rail_readings.py".
